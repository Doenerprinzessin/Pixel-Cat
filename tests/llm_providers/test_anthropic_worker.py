"""anthropic_client: the client, the model list, and AnthropicChat's request, stream, retries
and error mapping.

End-to-end tests run the real ``anthropic`` SDK against a fake Messages API on 127.0.0.1;
the error table and the stream details use a scripted fake client (``fake_sdk``)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from fakes.chat import RecordingEvents
from llm_providers.fakes import HISTORY, SSE, TOOLS, Json
from pixelcat.llm import anthropic_client as AC
from pixelcat.llm import ant_tool
from pixelcat.llm.external import SEARCHING, MemoryCredentials
from pixelcat.ports.llm import ChatRequest


INFO = {"adaptive": True, "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 128_000}
ASK = HISTORY[:2]


# ------------------------------------------------------------------ a fake Messages API
def msg_start() -> dict:
    return {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "content": [], "model": "claude-opus-5-5",
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50,
                  "cache_creation_input_tokens": 0}}}


def block(content_block: dict, *deltas: dict) -> list[dict]:
    return ([{"type": "content_block_start", "content_block": content_block}]
            + [{"type": "content_block_delta", "delta": d} for d in deltas]
            + [{"type": "content_block_stop"}])


def claude(*blocks: list[dict], stop: str = "end_turn") -> SSE:
    """A streamed message of these blocks (numbered in order)."""
    ev = [msg_start()]
    for i, b in enumerate(blocks):
        ev += [dict(e, index=i) for e in b]
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 450}}, {"type": "message_stop"}]
    return SSE(ev, named=True)


THINK = block({"type": "thinking", "thinking": "", "signature": ""},
              {"type": "thinking_delta", "thinking": "Let me see"}, {"type": "signature_delta", "signature": "SIG1"})
TEXT = block({"type": "text", "text": ""}, {"type": "text_delta", "text": "Hello "},
             {"type": "text_delta", "text": "there!"})
TOOL = block({"type": "tool_use", "id": "toolu_7", "name": "open_app", "input": {}},
             {"type": "input_json_delta", "partial_json": '{"name": "Note'},
             {"type": "input_json_delta", "partial_json": 'pad"}'})
SEARCH = block({"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}},
               {"type": "input_json_delta", "partial_json": '{"query": "cat news"}'})
RESULTS = block({"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
    {"type": "web_search_result", "url": "https://news.example.org/cats", "title": "Cat News",
     "encrypted_content": "E1", "page_age": None}]})
CITED = block({"type": "text", "text": "", "citations": []}, {"type": "text_delta", "text": "Cats are napping."},
              {"type": "citations_delta", "citation": {
                  "type": "web_search_result_location", "url": "https://news.example.org/cats", "title": "Cat News",
                  "encrypted_index": "I1", "cited_text": "Cats nap"}})


def bad_request(message: str, status: int = 400, kind: str = "invalid_request_error",
                headers: dict | None = None) -> Json:
    return Json(status, {"type": "error", "error": {"type": kind, "message": message}},
                {"x-should-retry": "false", **(headers or {})})


@pytest.fixture
def api(fake_http, monkeypatch):
    monkeypatch.setitem(AC.BASE_URLS, "anthropic", fake_http.url)
    return fake_http


@pytest.fixture
def key():
    return MemoryCredentials({"anthropic_key": "sk-ant-secret-123"})


def key_of(req) -> str:
    """The credential a request carried (the SDK sends it as x-api-key or as a bearer token)."""
    auth = req.header("authorization")
    return req.header("x-api-key") or (auth[7:] if auth.startswith("Bearer ") else "")


def bodies(api) -> list[dict]:
    return [r.json() for r in api.sent("/v1/messages")]


def run(record, model: str = "claude-opus-5-5", msgs: list | None = None, tools: list | None = None,
        think: bool = True, effort: str = "high", creds: Any = None, info: dict | None = INFO, web: bool = False,
        auth: str = "key"):
    w = AC.AnthropicChat(ChatRequest(model, ASK if msgs is None else msgs, tools, think, effort,
                                     creds or MemoryCredentials({"anthropic_key": "sk-ant-secret-123"}), auth,
                                     info or {}, web=web), RecordingEvents())
    rec = record(w)
    w.run()
    return w, rec


# ------------------------------------------------------------------ end to end (real SDK)
def test_text_thinking_and_tool_call(api, record):
    api.on("POST", "/v1/messages", claude(THINK, TEXT, TOOL, stop="tool_use"))
    w, rec = run(record, tools=TOOLS)
    assert rec.events == [("thinking", 1), ("thinking", 10), ("token", "Hello "), ("token", "there!"),
                          ("used", "anthropic", 600),
                          ("done", "Hello there!", [{"id": "toolu_7", "name": "open_app",
                                                     "arguments": {"name": "Notepad"}}])]
    assert w.raw == [{"type": "thinking", "thinking": "Let me see", "signature": "SIG1"},
                     {"type": "text", "text": "Hello there!"},
                     {"type": "tool_use", "id": "toolu_7", "name": "open_app", "input": {"name": "Notepad"}}]


def test_request_body_and_headers(api, record):
    api.on("POST", "/v1/messages", claude(TEXT))
    run(record, tools=TOOLS)
    req = api.sent("/v1/messages")[0]
    body = req.json()
    assert key_of(req) == "sk-ant-secret-123"
    assert set(req.header("anthropic-beta").split(",")) == {AC.BETA_BINDING, AC.BETA_FALLBACK}
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 32_000
    assert body["system"] == [{"type": "text", "text": "You are the cat."}]
    assert body["messages"] == AC.to_claude(ASK)[1]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert body["output_config"] == {"effort": "high"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert [t["name"] for t in body["tools"]] == ["open_app", "get_time"]
    assert all(t["eager_input_streaming"] for t in body["tools"])
    assert body["stream"] is True


def test_model_without_fallbacks_and_thinking_off(api, record):
    api.on("POST", "/v1/messages", claude(TEXT))
    run(record, model="claude-haiku-5-5", think=False, effort="max", info={"max_tokens": 8000})
    req = api.sent("/v1/messages")[0]
    body = req.json()
    assert "fallbacks" not in body and "tools" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"] == {"effort": "high"}          # thinking off: at most high
    assert body["max_tokens"] == 8000
    assert req.header("anthropic-beta") == ""


def test_web_search_tool_status_and_sources(api, record):
    api.on("POST", "/v1/messages", claude(SEARCH, RESULTS, CITED))
    _w, rec = run(record, tools=TOOLS, web=True)
    tools = bodies(api)[0]["tools"]
    assert tools[-1] == {"type": "web_search_20250305", "name": "web_search", "max_uses": AC.WEB_SEARCHES}
    assert ("status", SEARCHING) in rec.events
    assert rec.done == ("Cats are napping.\n\n**Sources:** [Cat News](https://news.example.org/cats)", [])


@pytest.mark.parametrize("web, info", [(False, INFO), (True, dict(INFO, web_search=False))])
def test_no_web_search_tool_when_off_or_unsupported(api, record, web, info):
    api.on("POST", "/v1/messages", claude(TEXT))
    run(record, tools=TOOLS, web=web, info=info)
    assert all(t.get("name") != "web_search" for t in bodies(api)[0]["tools"])


def test_paused_reply_is_continued(api, record):
    api.on("POST", "/v1/messages", claude(SEARCH, stop="pause_turn"), claude(SEARCH, RESULTS, CITED))
    _w, rec = run(record, web=True)
    sent = bodies(api)
    assert len(sent) == 2
    cont = sent[1]["messages"][-1]
    assert cont["role"] == "assistant" and cont["content"][0]["type"] == "server_tool_use"
    assert rec.of("used") == [("anthropic", 600), ("anthropic", 600)]
    assert rec.done[0].startswith("Cats are napping.")


def test_refusal_and_truncated_tool_call(api, record):
    api.on("POST", "/v1/messages", claude(TEXT, stop="refusal"))
    _w, rec = run(record)
    assert rec.failed == ("refused", "")
    api.on("POST", "/v1/messages", claude(TEXT, TOOL, stop="max_tokens"))
    _w, rec = run(record, tools=TOOLS)
    assert rec.failed == ("truncated", "") and rec.done is None


def test_max_tokens_without_calls_is_a_normal_answer(api, record):
    api.on("POST", "/v1/messages", claude(TEXT, stop="max_tokens"))
    _w, rec = run(record)
    assert rec.done == ("Hello there!", [])


def test_signed_in_requests_use_the_sign_in_not_the_key(api, record, monkeypatch):
    d = ant_tool.signin_dir()
    (d / "configs").mkdir(parents=True)
    (d / "credentials").mkdir(parents=True)
    (d / "configs" / "default.json").write_text(json.dumps({"authentication": {"type": "user_oauth"}}))
    (d / "credentials" / "default.json").write_text(json.dumps({"access_token": "tok-signin",
                                                                "expires_at": int(time.time()) + 3600}))
    (d / "credentials" / "default.json").chmod(0o600)        # (the SDK refuses a token file others can read)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    api.on("POST", "/v1/messages", claude(TEXT))
    _w, rec = run(record, auth="signin")
    req = api.sent("/v1/messages")[0]
    assert key_of(req) == "tok-signin"
    assert rec.done == ("Hello there!", [])
    api.on("GET", "/v1/models", Json(200, {"data": [], "has_more": False}))
    assert AC.signin_status(MemoryCredentials({})) is True


def test_signin_status_false_without_a_sign_in(api):
    assert AC.signin_status(MemoryCredentials({"anthropic_key": "k"})) is False


# ------------------------------------------------------------------ settings a model refuses
def _thinking(b: dict) -> Any:
    return (b.get("thinking") or {}).get("type")


@pytest.mark.parametrize("message, think, check", [
    ("thinking.type: 'disabled' is not supported for this model", False,
     lambda a, b: (_thinking(a), _thinking(b)) == ("disabled", "between_tools")),
    ("thinking.type: adaptive is not supported", True,
     lambda a, b: b["thinking"] == {"type": "enabled", "budget_tokens": 4096} and "output_config" not in b),
    ("fallbacks: not available", True, lambda a, b: "fallbacks" in a and "fallbacks" not in b),
    ("thinking.block_binding: unknown field", True,
     lambda a, b: "block_binding" in a["thinking"] and "block_binding" not in b["thinking"]),
    ("tools.0.eager_input_streaming: Extra inputs are not permitted", True,
     lambda a, b: "eager_input_streaming" in a["tools"][0] and "eager_input_streaming" not in b["tools"][0]),
    ("cache_control: not supported", True, lambda a, b: "cache_control" in a and "cache_control" not in b),
    ("output_config.effort: Extra inputs are not permitted", True,
     lambda a, b: "output_config" in a and "output_config" not in b),
    ("Invalid `signature` in `thinking` block", True,
     lambda a, b: any(x["type"] == "thinking" for m in a["messages"] for x in m["content"])
     and not any(x["type"] == "thinking" for m in b["messages"] for x in m["content"])),
])
def test_a_refused_setting_is_dropped_and_asked_again(api, record, message, think, check):
    api.on("POST", "/v1/messages", bad_request(message), claude(TEXT))
    _w, rec = run(record, msgs=HISTORY[:8], tools=TOOLS, think=think)
    first, second = bodies(api)
    assert check(first, second), (first, second)
    assert rec.done == ("Hello there!", [])


def test_thinking_off_steps_down_to_always_and_is_remembered(api, record):
    api.on("POST", "/v1/messages", bad_request("thinking disabled not supported"),
           bad_request("thinking between_tools not supported"), claude(TEXT))
    w, rec = run(record, model="claude-sonnet-5-5", think=False, effort="medium")
    sent = bodies(api)
    assert [_thinking(b) for b in sent] == ["disabled", "between_tools", None]
    assert sent[2]["output_config"] == {"effort": "low"}
    assert "always thinks" in w.note and rec.done is not None
    w2, _rec = run(record, model="claude-sonnet-5-5", think=False, effort="medium")
    assert [_thinking(b) for b in bodies(api)[3:]] == [None]
    assert "always thinks" in w2.note


def test_nothing_left_to_step_down_is_an_error(api, record):
    api.on("POST", "/v1/messages", bad_request("thinking: nope"))
    _w, rec = run(record, think=True, info=dict(INFO, adaptive=False, enabled=False))
    assert [_thinking(b) for b in bodies(api)] == [None]
    assert rec.failed == ("error", "thinking: nope")


def test_a_bad_signature_twice_is_an_error(api, record):
    api.on("POST", "/v1/messages", bad_request("bad signature"))
    _w, rec = run(record, msgs=HISTORY[:8])
    assert len(bodies(api)) == 2 and rec.failed == ("error", "bad signature")


def test_web_search_off_for_the_organization(api, record):
    api.on("POST", "/v1/messages", bad_request("Web search is not enabled for this organization."), claude(TEXT))
    w, rec = run(record, web=True)
    assert [any(t.get("name") == "web_search" for t in b.get("tools", [])) for b in bodies(api)] == [True, False]
    assert "turned off" in w.note and rec.done is not None
    assert AC._learned["claude-opus-5-5"] == {"no_search": True}


def test_learned_settings_are_used_from_the_start(api, record):
    AC._learned["claude-opus-5-5"] = {"no_cache": True, "no_fallback": True, "thinking_on": "enabled"}
    api.on("POST", "/v1/messages", claude(TEXT))
    run(record)
    body = bodies(api)[0]
    assert "cache_control" not in body and "fallbacks" not in body
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}


@pytest.mark.parametrize("effort, think, levels, sent", [
    ("max", True, ["low", "medium", "high"], "high"),
    ("xhigh", False, None, "high"),
    ("low", True, ["medium", "high"], "medium"),
    ("auto", True, ["low", "high"], "low"),
    ("medium", True, None, "medium"),
    ("high", True, [], None),
])
def test_effort_sent(api, record, effort, think, levels, sent):
    api.on("POST", "/v1/messages", claude(TEXT))
    info = {"adaptive": True} if levels is None else {"adaptive": True, "effort": levels}
    run(record, think=think, effort=effort, info=info)
    assert (bodies(api)[0].get("output_config") or {}).get("effort") == sent


# ------------------------------------------------------------------ the model list and the client
MODELS = {"data": [
    {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
     "created_at": "2026-09-01T00:00:00Z", "max_input_tokens": 1_000_000, "max_tokens": 128_000,
     "capabilities": {"thinking": {"supported": True, "types": {"adaptive": {"supported": True},
                                                                "enabled": {"supported": False}}},
                      "effort": {"supported": True, "low": {"supported": True}, "high": {"supported": True},
                                 "max": {"supported": True}},
                      "image_input": {"supported": True}, "server_tools": {"web_search": {"supported": False}}}},
    {"type": "model", "id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5",
     "created_at": "2026-09-01T00:00:00Z"}],
    "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-haiku-5-5"}


def test_list_models(api, key):
    api.on("GET", "/v1/models", Json(200, MODELS))
    ms = AC.list_models(key)
    assert ms[0] == {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "adaptive": True, "enabled": False,
                     "effort": ["low", "high", "max"], "vision": True, "max_tokens": 128_000, "context": 1_000_000,
                     "web_search": False}
    assert ms[1] == {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "adaptive": False, "enabled": False,
                     "effort": [], "vision": True, "max_tokens": 0, "context": 0, "web_search": True}
    assert api.sent("/v1/models")[0].query == {"limit": "100"}


def test_list_models_raises_the_sdks_errors(api):
    api.on("GET", "/v1/models", bad_request("invalid x-api-key", 401, "authentication_error"))
    with pytest.raises(anthropic.AuthenticationError):
        AC.list_models(MemoryCredentials({"anthropic_key": "sk-ant-bad"}))


def test_client_needs_a_key_or_a_sign_in():
    with pytest.raises(anthropic.CredentialsError, match="No Anthropic API key"):
        AC.client(MemoryCredentials({}))
    with pytest.raises(anthropic.CredentialsError, match="sign in again"):
        AC.client(MemoryCredentials({"anthropic_key": "k"}), "signin")


def test_client_options(monkeypatch):
    c = AC.client(MemoryCredentials({"anthropic_key": "k"}), timeout=12.0)
    assert (c.api_key, c.max_retries, c.timeout) == ("k", 2, 12.0)
    monkeypatch.setitem(AC.BASE_URLS, "anthropic", "http://127.0.0.1:9/")
    assert str(AC.client(MemoryCredentials({"anthropic_key": "k"})).base_url).startswith("http://127.0.0.1:9")


# ------------------------------------------------------------------ a scripted SDK (errors, stream details)
_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls: type, status: int, message: str = "nope", headers: dict | None = None, body: Any = "default"):
    resp = httpx2.Response(status, request=_REQ, headers=headers or {})
    return cls(message, response=resp, body={"error": {"message": message}} if body == "default" else body)


class FakeBlock(dict):
    def to_dict(self) -> dict:
        return dict(self)


class FakeStream:
    def __init__(self, events: list, final: Any, on_event: Any = None) -> None:
        self.events, self.final, self.on_event = events, final, on_event

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def __iter__(self):
        for ev in self.events:
            if self.on_event:
                self.on_event(ev)
            yield ev

    def get_final_message(self) -> Any:
        return self.final


@pytest.fixture
def fake_sdk(monkeypatch):
    """``fake_sdk.script``: per request an exception to raise or a FakeStream."""
    sdk = SimpleNamespace(script=[], calls=[])

    def stream(**params: Any) -> Any:
        sdk.calls.append(params)
        item = sdk.script.pop(0) if len(sdk.script) > 1 else sdk.script[0]
        if isinstance(item, BaseException):
            raise item
        return item

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    monkeypatch.setattr(AC, "client", lambda creds, auth="key", timeout=600.0: client)
    return sdk


def final(*blocks: dict, stop: str = "end_turn", usage: dict | None = None, **kw: Any) -> Any:
    u = SimpleNamespace(**(usage or {"input_tokens": 10, "output_tokens": 5}))
    return SimpleNamespace(content=[FakeBlock(b) for b in blocks], stop_reason=stop, usage=u, **kw)


def ev(kind: str, **kw: Any) -> Any:
    return SimpleNamespace(type=kind, **kw)


def delta(kind: str, **kw: Any) -> Any:
    return ev("content_block_delta", delta=SimpleNamespace(type=kind, **kw))


def future_iso(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("exc, kind, detail", [
    (status_error(anthropic.BadRequestError, 400, "prompt is too long: 250000 tokens > 200000 maximum"),
     "too_long", None),
    (status_error(anthropic.BadRequestError, 400, "something odd"), "error", "something odd"),
    (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "auth", "invalid x-api-key"),
    (status_error(anthropic.PermissionDeniedError, 403, "not for you"), "denied", "not for you"),
    (status_error(anthropic.NotFoundError, 404, "model: claude-x"), "model_gone", "model: claude-x"),
    (status_error(anthropic.InternalServerError, 529, "Overloaded"), "busy", "Overloaded"),
    (status_error(anthropic.APIStatusError, 418, "teapot", body=None), "error", "HTTP 418: teapot"),
    (status_error(anthropic.APIStatusError, 409, "x", body={"message": "flat body"}), "error", "HTTP 409: flat body"),
    (anthropic.APITimeoutError(request=_REQ), "timeout", ""),
    (anthropic.APIConnectionError(message="Connection error.", request=_REQ), "ext_offline", "Connection error."),
    (anthropic.AnthropicError("no credentials found"), "auth", "no credentials found"),
    (RuntimeError("boom"), "error", "boom"),
])
def test_errors_are_mapped(fake_sdk, record, exc, kind, detail):
    fake_sdk.script = [exc]
    _w, rec = run(record)
    assert rec.kinds == ["failed"] and rec.failed[0] == kind
    if detail is not None:
        assert rec.failed[1] == detail
    else:
        assert json.loads(rec.failed[1])["limit"] == 200_000
    assert len(fake_sdk.calls) == 1


@pytest.mark.parametrize("exc, why, has_reset", [
    (status_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the API."),
     "credits", False),
    (status_error(anthropic.PaymentRequiredError, 402, "pay up"), "credits", False),
    (status_error(anthropic.RateLimitError, 429, "You hit your spend limit"), "spend", False),
    (status_error(anthropic.RateLimitError, 429, "slow down"), "rate", False),
    (status_error(anthropic.RateLimitError, 429, "slow down", {"retry-after": "30"}), "rate", True),
])
def test_limits(fake_sdk, record, exc, why, has_reset):
    fake_sdk.script = [exc]
    _w, rec = run(record)
    lim = rec.limit()
    assert (lim["provider"], lim["why"], lim["reset"] is not None) == ("anthropic", why, has_reset)
    assert lim["detail"] == exc.body["error"]["message"]


@pytest.mark.parametrize("headers, minutes", [
    (lambda: {"anthropic-ratelimit-tokens-remaining": "0", "anthropic-ratelimit-tokens-reset": future_iso(10),
              "anthropic-ratelimit-requests-remaining": "5", "anthropic-ratelimit-requests-reset": future_iso(1),
              "retry-after": "5"}, 10),                                       # the bucket that ran out
    (lambda: {"anthropic-ratelimit-requests-remaining": "5", "anthropic-ratelimit-requests-reset": future_iso(3),
              "anthropic-ratelimit-input-tokens-reset": future_iso(7)}, 7),  # none ran out: the latest
    (lambda: {"anthropic-ratelimit-requests-reset": future_iso(3), "retry-after": "60"}, 1),   # retry-after first
    (lambda: {"anthropic-ratelimit-tokens-reset": "2000-01-01T00:00:00Z",
              "anthropic-ratelimit-output-tokens-reset": "bad", "retry-after": "soon"}, None),
    (lambda: {}, None),
], ids=["empty-bucket", "latest-other", "retry-after", "past-or-bad", "none"])
def test_rate_limit_reset(fake_sdk, record, headers, minutes):
    fake_sdk.script = [status_error(anthropic.RateLimitError, 429, "slow down", headers())]
    _w, rec = run(record)
    reset = rec.limit()["reset"]
    if minutes is None:
        assert reset is None
    else:
        assert abs(reset - (time.time() + minutes * 60)) < 5


def test_connection_error_after_cancel_is_quiet(fake_sdk, record, monkeypatch):
    w = AC.AnthropicChat(ChatRequest("claude-opus-5-5", ASK, None, True, "high", MemoryCredentials({}), "key", INFO),
                         RecordingEvents())
    rec = record(w)

    def stream(**params: Any) -> Any:                # the user cancels; closing the stream drops the connection
        fake_sdk.calls.append(params)
        w.cancel()
        raise anthropic.APIConnectionError(request=_REQ)

    monkeypatch.setattr(AC, "client", lambda creds, auth="key", timeout=600.0:
                        SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))))
    w.run()
    assert len(fake_sdk.calls) == 1 and rec.events == []


def test_connection_error_without_cancel_is_reported(fake_sdk, record):
    fake_sdk.script = [anthropic.APIConnectionError(request=_REQ)]
    _w, rec = run(record)
    assert rec.failed[0] == "ext_offline" and len(fake_sdk.calls) == 1


def test_unreadable_tool_json_is_asked_again_up_to_eight_times(fake_sdk, record):
    fake_sdk.script = [ValueError("bad json")]
    _w, rec = run(record)
    assert len(fake_sdk.calls) == 8
    assert rec.events == []                  # current behaviour: gives up without a signal


def test_value_error_then_success(fake_sdk, record):
    fake_sdk.script = [ValueError("bad json"), FakeStream([], final({"type": "text", "text": "ok"}))]
    _w, rec = run(record)
    assert rec.done == ("ok", []) and len(fake_sdk.calls) == 2


def test_stream_events_are_reported(fake_sdk, record):
    events = [ev("content_block_start", content_block=SimpleNamespace(type="thinking")),
              delta("thinking_delta", thinking="abc"), delta("thinking_delta", thinking=""),
              ev("content_block_start", content_block=SimpleNamespace(type="thinking")),
              ev("content_block_start", content_block=SimpleNamespace(type="server_tool_use")),
              delta("text_delta", text="Hi"), delta("text_delta", text=""), delta("input_json_delta", partial_json="{"),
              ev("message_delta")]
    usage = {"input_tokens": 3, "output_tokens": 4, "cache_creation_input_tokens": 5, "cache_read_input_tokens": None,
             "server_tool_use": SimpleNamespace(web_search_requests=2)}
    fake_sdk.script = [FakeStream(events, final({"type": "text", "text": "Hi"}, usage=usage))]
    _w, rec = run(record)
    assert rec.events == [("thinking", 1), ("thinking", 3), ("thinking", 4), ("thinking", 4), ("status", SEARCHING),
                          ("token", "Hi"), ("used", "anthropic", 12), ("done", "Hi", [])]


def test_cancel_while_streaming_reports_nothing(fake_sdk, record):
    w = AC.AnthropicChat(ChatRequest("claude-opus-5-5", ASK, None, True, "high", MemoryCredentials({}), "key", INFO),
                         RecordingEvents())
    rec = record(w)
    fake_sdk.script = [FakeStream([delta("text_delta", text="a"), delta("text_delta", text="b")],
                                  final({"type": "text", "text": "ab"}), on_event=lambda e: w.cancel())]
    w.run()
    assert rec.events == []


def test_after_a_fallback_the_declined_attempt_is_dropped(fake_sdk, record):
    fake_sdk.script = [FakeStream([], final(
        {"type": "thinking", "thinking": "old", "signature": "s"},
        {"type": "text", "text": "Before. "},
        {"type": "tool_use", "id": "t_old", "name": "a", "input": {}},
        {"type": "fallback", "model": "claude-sonnet-5-5"},
        {"type": "thinking", "thinking": "new", "signature": "s2"},
        {"type": "text", "text": "After.", "citations": [{"url": "https://x.example/", "title": "X"}, "junk"]},
        {"type": "tool_use", "id": "t_new", "name": "b", "input": "not a dict"},
        stop="tool_use"))]
    w, rec = run(record)
    assert rec.done == ("Before. After.\n\n**Sources:** [X](https://x.example/)",
                        [{"id": "t_new", "name": "b", "arguments": {}}])
    assert [b["type"] for b in w.raw] == ["text", "fallback", "thinking", "text", "tool_use"]


def test_refusal_reports_its_category(fake_sdk, record):
    fake_sdk.script = [FakeStream([], final({"type": "text", "text": ""}, stop="refusal",
                                            stop_details=SimpleNamespace(category="cyber")))]
    w, rec = run(record)
    assert rec.events[-1] == ("failed", "refused", "cyber") and w.raw is None
