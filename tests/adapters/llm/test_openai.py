"""openai_client: Responses API input, tools, headers, the model list, and OpenAIChat's
streamed events, retries and error mapping - against a fake API on 127.0.0.1."""
from __future__ import annotations

import copy
import time
from typing import Any

import pytest
import requests

from fakes.chat import RecordingEvents
from adapters.llm.fakes import HISTORY, JPEG, PNG, SSE, TOOLS, Json, Raw
from pixelcat.adapters.llm import chatgpt_signin
from pixelcat.adapters.llm import openai_client as OC
from pixelcat.llm.external import SEARCHING, MemoryCredentials
from pixelcat.ports.llm import ChatRequest

ASK = HISTORY[:2]


# ------------------------------------------------------------------ messages -> Responses input
def test_full_history_as_responses_input():
    instructions, items = OC.to_responses(HISTORY)
    assert instructions == "You are the cat."
    assert items == [
        {"role": "user", "content": [{"type": "input_text", "text": "open notepad and look"},
                                     {"type": "input_image", "image_url": f"data:image/png;base64,{PNG}"}]},
        {"role": "assistant", "content": "On it."},
        {"type": "function_call", "call_id": "call_2_0", "name": "open_app", "arguments": '{"name": "notepad"}'},
        {"type": "function_call", "call_id": "call_2_1", "name": "take_screenshot", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_2_0", "output": "opened Notepad"},
        {"type": "function_call_output", "call_id": "call_2_1", "output": "here it is"},
        {"role": "user", "content": [{"type": "input_text", "text": "(the picture from take_screenshot)"},
                                     {"type": "input_image", "image_url": f"data:image/jpeg;base64,{JPEG}"}]},
        {"role": "user", "content": "(note from the app)"},
        {"role": "assistant", "content": "Typing."},
        {"type": "function_call", "call_id": "toolu_x", "name": "type_text", "arguments": '{"text": "hi"}'},
        {"type": "function_call_output", "call_id": "toolu_x", "output": "typed"},
        {"type": "function_call", "call_id": "call_8_0", "name": "press_keys", "arguments": '{"keys": "x"}'},
        {"type": "function_call_output", "call_id": "call_8_0", "output": "(not run - the user stopped it)"},
        {"role": "user", "content": "stop that, do something else"},
    ]


def test_history_is_not_changed():
    before = copy.deepcopy(HISTORY)
    OC.to_responses(HISTORY)
    assert HISTORY == before


def test_more_system_messages_empty_turns_and_orphan_results():
    instructions, items = OC.to_responses([
        {"role": "system", "content": "one"}, {"role": "system", "content": "two"},
        {"role": "user", "content": " "}, {"role": "user", "content": "", "images": [PNG]},
        {"role": "assistant", "content": ""},
        {"role": "tool", "tool_name": "look", "content": "seen", "images": [JPEG]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "a", "arguments": "x"}}]},
        {"role": "tool", "content": ""},
    ])
    assert instructions == "one"
    assert items == [
        {"role": "developer", "content": "two"},
        {"role": "user", "content": [{"type": "input_image", "image_url": f"data:image/png;base64,{PNG}"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "[result of look] seen"},
                                     {"type": "input_image", "image_url": f"data:image/jpeg;base64,{JPEG}"}]},
        {"type": "function_call", "call_id": "c", "name": "a", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "(done)"},
    ]


def test_tools_for_responses():
    assert OC.tools_for_responses(TOOLS) == [
        {"type": "function", "name": "open_app", "description": "Open an app", "strict": False,
         "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}},
        {"type": "function", "name": "get_time", "description": "", "strict": False,
         "parameters": {"type": "object", "properties": {}}}]
    assert OC.tools_for_responses(None) == []


# ------------------------------------------------------------------ headers
def test_headers_with_a_key_or_the_chatgpt_sign_in(monkeypatch):
    assert OC.headers(MemoryCredentials({"openai_key": "sk-1"}), "key") == {
        "Authorization": "Bearer sk-1", "Content-Type": "application/json"}
    with pytest.raises(OC.OpenAIError) as e:
        OC.headers(MemoryCredentials({}), "key")
    assert e.value.kind == "auth"
    forced = []
    monkeypatch.setattr(chatgpt_signin, "access_token", lambda creds, force=False: forced.append(force) or "AT")
    assert OC.headers(MemoryCredentials({}), "chatgpt", True)["Authorization"] == "Bearer AT"
    assert forced == [True]

    def refuse(creds, force=False):
        raise chatgpt_signin.SignInError("Not signed in with ChatGPT.")
    monkeypatch.setattr(chatgpt_signin, "access_token", refuse)
    with pytest.raises(OC.OpenAIError, match="Not signed in") as e:
        OC.headers(MemoryCredentials({}), "chatgpt")
    assert e.value.kind == "auth"


# ------------------------------------------------------------------ the fake API
@pytest.fixture
def api(fake_http, monkeypatch):
    monkeypatch.setitem(OC.BASE_URLS, "openai", fake_http.url + "/oa/")
    return fake_http


@pytest.fixture
def chatgpt(monkeypatch):
    """The ChatGPT sign-in: 'AT1', and 'AT2' once renewed (force)."""
    calls: list[bool] = []

    def token(creds, force=False):
        calls.append(force)
        return "AT2" if force else "AT1"
    monkeypatch.setattr(chatgpt_signin, "access_token", token)
    return calls


def oa(*events: dict, usage: dict | None = None, output: list | None = None, end: str = "completed") -> SSE:
    final = {"type": f"response.{end}", "response": {"id": "resp_1", "output": output or [],
                                                     "usage": usage or {"input_tokens": 300, "output_tokens": 40,
                                                                        "total_tokens": 340}}}
    return SSE([{"type": "response.created", "response": {"id": "resp_1"}}, *events, final], done=True)


def text(*pieces: str) -> list[dict]:
    return [{"type": "response.output_text.delta", "delta": p} for p in pieces]


def call(cid: str, name: str, args: str) -> dict:
    return {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_" + cid,
                                                          "call_id": cid, "name": name, "arguments": args}}


REASONING = {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}}


def api_error(status: int, message: str, code: str = "", param: str = "", headers: dict | None = None) -> Json:
    return Json(status, {"error": {"message": message, "type": "invalid_request_error", "param": param or None,
                                   "code": code or None}}, headers or {})


def run(record, model: str = "gpt-6.1", msgs: list | None = None, tools: list | None = None, think: bool = True,
        effort: str = "high", auth: str = "key", web: bool = False, creds: Any = None):
    w = OC.OpenAIChat(ChatRequest(model, ASK if msgs is None else msgs, tools, think, effort,
                                  creds or MemoryCredentials({"openai_key": "sk-oa-1"}), auth, web=web),
                      RecordingEvents())
    rec = record(w)
    w.run()
    return w, rec


def bodies(api) -> list[dict]:
    return [r.json() for r in api.sent("/oa/responses")]


# ------------------------------------------------------------------ the worker
def test_text_reasoning_tool_call_and_usage(api, record):
    api.on("POST", "/oa/responses", oa(REASONING, *text("Hi ", "from GPT"),
                                       call("call_9", "open_app", '{"name": "Paint"}'),
                                       call("call_10", "get_time", "not json"),
                                       call("call_11", "get_time", "[1]")))
    w, rec = run(record, tools=TOOLS)
    assert rec.events == [
        ("thinking", 1), ("token", "Hi "), ("token", "from GPT"), ("used", "openai", 340),
        ("done", "Hi from GPT", [{"id": "call_9", "name": "open_app", "arguments": {"name": "Paint"}},
                                 {"id": "call_10", "name": "get_time", "arguments": {}},
                                 {"id": "call_11", "name": "get_time", "arguments": {}}])]
    assert w.raw is None


def test_request_body_and_headers(api, record):
    api.on("POST", "/oa/responses", oa(*text("x")))
    run(record, tools=TOOLS)
    req = api.sent("/oa/responses")[0]
    body = req.json()
    assert req.header("authorization") == "Bearer sk-oa-1"
    assert body == {"model": "gpt-6.1", "input": OC.to_responses(ASK)[1], "store": False, "stream": True,
                    "instructions": "You are the cat.", "tools": OC.tools_for_responses(TOOLS),
                    "reasoning": {"effort": "high"}}


@pytest.mark.parametrize("think, effort, sent", [
    (True, "max", "xhigh"), (True, "xhigh", "xhigh"), (True, "high", "high"), (True, "medium", "medium"),
    (True, "low", "low"), (True, "auto", "medium"), (False, "max", "none"),
])
def test_reasoning_effort(api, record, think, effort, sent):
    api.on("POST", "/oa/responses", oa(*text("x")))
    run(record, think=think, effort=effort)
    assert bodies(api)[0]["reasoning"] == {"effort": sent}


def test_reasoning_summaries_count_as_thinking(api, record):
    api.on("POST", "/oa/responses", oa({"type": "response.reasoning_summary_text.delta", "delta": "abcd"},
                                       {"type": "response.reasoning_text.delta", "delta": ""},
                                       *text("ok")))
    _w, rec = run(record)
    assert rec.of("thinking") == [(4,), (5,)]


def test_web_search(api, record):
    output = [{"type": "message", "content": [{"type": "output_text", "text": "Cats rule.", "annotations": [
        {"type": "url_citation", "url": "https://daily.example.com/x", "title": "Cat Daily"},
        {"type": "url_citation", "url": "https://wiki.example.org/Cat", "title": "Cat - Wiki"},
        {"type": "file_citation", "file_id": "f"}]}]}]
    api.on("POST", "/oa/responses", oa(
        {"type": "response.output_item.added", "item": {"type": "web_search_call", "id": "ws_1"}},
        *text("Cats rule ([Cat Daily](https://daily.example.com/x))."), output=output))
    _w, rec = run(record, tools=TOOLS, web=True)
    assert bodies(api)[0]["tools"][-1] == {"type": "web_search"}
    assert ("status", SEARCHING) in rec.events
    assert rec.done[0] == ("Cats rule ([Cat Daily](https://daily.example.com/x)).\n\n"
                           "**Sources:** [Cat - Wiki](https://wiki.example.org/Cat)")


def test_completed_without_deltas_uses_the_output_text(api, record):
    output = [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": "A"},
                                                                     {"type": "refusal", "refusal": "no"},
                                                                     {"type": "output_text", "text": "B"}]}]
    api.on("POST", "/oa/responses", oa(output=output, usage={"input_tokens": 1, "output_tokens": 2}))
    _w, rec = run(record)
    assert rec.events == [("used", "openai", 3), ("done", "AB", [])]


def test_no_usage_no_used_signal(api, record):
    api.on("POST", "/oa/responses", oa(*text("x"), usage={"total_tokens": 0}))
    _w, rec = run(record)
    assert rec.kinds == ["token", "done"]


@pytest.mark.parametrize("reason, with_call, expected", [
    ("content_filter", False, ("failed", "refused", "content_filter")),
    ("max_output_tokens", True, ("failed", "truncated", "max_output_tokens")),
    ("max_output_tokens", False, ("done", "partial", [])),
])
def test_incomplete_answers(api, record, reason, with_call, expected):
    events = [*text("partial")] + ([call("c1", "a", "{}")] if with_call else [])
    sse = oa(*events, end="incomplete")
    sse.events[-1]["response"]["incomplete_details"] = {"reason": reason}
    api.on("POST", "/oa/responses", sse)
    _w, rec = run(record)
    assert rec.events[-1] == expected
    assert ("used", "openai", 340) in rec.events


@pytest.mark.parametrize("event, expected", [
    ({"type": "response.failed", "response": {"error": {"code": "context_length_exceeded", "message": "too big"}}},
     "too_long"),
    ({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded",
                                                        "message": "plan"}}}, "limit:plan"),
    ({"type": "response.failed", "response": {"error": {"code": "insufficient_quota", "message": "q"}}},
     "limit:credits"),
    ({"type": "error", "code": "rate_limit_exceeded", "message": "slow"}, "limit:rate"),
    ({"type": "error", "code": "server_overloaded", "message": "busy"}, "busy"),
    ({"type": "error", "code": "service_unavailable", "message": "down"}, "busy"),
    ({"type": "response.failed", "response": {"error": "not a dict"}}, "error"),
    ({"type": "error", "code": "unsupported_value", "message": "nothing to change", "param": "temperature"}, "error"),
])
def test_failed_events(api, record, event, expected):
    api.on("POST", "/oa/responses", SSE([{"type": "response.created"}, event]))
    _w, rec = run(record)
    kind = rec.failed[0]
    assert (kind if kind != "limit" else f"limit:{rec.limit()['why']}") == expected
    if expected == "error" and event["type"] == "response.failed":
        assert rec.failed[1] == "the answer failed"


def test_failed_event_with_a_refused_effort_is_asked_again(api, record):
    api.on("POST", "/oa/responses",
           SSE([{"type": "error", "code": "unsupported_parameter", "param": "reasoning.effort", "message": "no"}]),
           oa(*text("ok")))
    _w, rec = run(record, effort="high")
    assert [b.get("reasoning") for b in bodies(api)] == [{"effort": "high"}, None]
    assert rec.done == ("ok", [])


def test_stream_that_breaks_off(api, record):
    api.on("POST", "/oa/responses", Raw(200, b"event: x\n\ndata: {broken\n\ndata: {\"type\": \"response.output_text"
                                             b".delta\", \"delta\": \"Hi\"}\n\ndata: [DONE]\n\ndata: {}\n\n",
                                        {"Content-Type": "text/event-stream"}))
    _w, rec = run(record)
    assert rec.events == [("token", "Hi"), ("failed", "error", "The answer broke off before it was complete.")]


@pytest.mark.parametrize("response, expected", [
    (api_error(400, "Your input exceeds the context window of this model.", "context_length_exceeded", "input"),
     "too_long"),
    (api_error(429, "limit", "subscription_sharing_usage_limit_exceeded"), "limit:plan"),
    (api_error(429, "You exceeded your current quota", "insufficient_quota"), "limit:credits"),
    (api_error(429, "Rate limit reached"), "limit:rate"),
    (api_error(403, "no", "subscription_sharing_user_not_eligible"), "denied"),
    (api_error(403, "Project does not have access"), "denied"),
    (api_error(401, "Incorrect API key provided"), "auth"),
    (api_error(404, "The model does not exist", "model_not_found"), "model_gone"),
    (api_error(500, "server error"), "busy"),
    (api_error(503, "overloaded"), "busy"),
    (api_error(418, "teapot"), "error"),
    (api_error(400, "Unsupported parameter: 'temperature'", "unsupported_parameter", "temperature"), "error"),
    (Raw(502, b"<html>Bad gateway</html>"), "busy"),
])
def test_http_errors_are_mapped(api, record, response, expected):
    api.on("POST", "/oa/responses", response)
    _w, rec = run(record)
    kind = rec.failed[0]
    assert (kind if kind != "limit" else f"limit:{rec.limit()['why']}") == expected
    assert len(api.sent("/oa/responses")) == 1


def test_http_error_detail(api, record):
    api.on("POST", "/oa/responses", Raw(502, b"<html>Bad gateway</html>"))
    _w, rec = run(record)
    assert rec.failed == ("busy", "<html>Bad gateway</html>")
    api.on("POST", "/oa/responses", Json(500, {}))
    _w, rec = run(record)
    assert rec.failed == ("busy", "HTTP 500")


@pytest.mark.parametrize("headers, seconds", [
    ({"x-ratelimit-reset-tokens": "6m0s", "x-ratelimit-reset-requests": "1.5s", "retry-after": "20"}, 360),
    ({"x-ratelimit-reset-requests": "1h2m"}, 3720),
    ({"retry-after": "45"}, 45),
    ({"x-ratelimit-reset-tokens": "20ms"}, 0.02),
    ({"retry-after": "soon", "x-ratelimit-reset-tokens": "later"}, None),
])
def test_rate_limit_reset(api, record, headers, seconds):
    api.on("POST", "/oa/responses", api_error(429, "Rate limit reached", headers=headers))
    _w, rec = run(record)
    reset = rec.limit()["reset"]
    if seconds is None:
        assert reset is None
    else:
        assert abs(reset - (time.time() + seconds)) < 3


def test_effort_steps_down_and_is_remembered(api, record):
    refuse = api_error(400, "Unsupported value: 'reasoning.effort'", "unsupported_value", "reasoning.effort")
    api.on("POST", "/oa/responses", refuse, refuse, oa(*text("ok")))
    _w, rec = run(record, model="o9", think=False)
    assert [b.get("reasoning") for b in bodies(api)] == [{"effort": "none"}, {"effort": "minimal"}, None]
    assert rec.done == ("ok", [])
    assert OC._learned == {"o9|False|high": {"effort": None}}
    run(record, model="o9", think=False)
    assert bodies(api)[3].get("reasoning") is None


def test_effort_with_nothing_left_is_an_error(api, record):
    api.on("POST", "/oa/responses", api_error(400, "reasoning is not supported", "unsupported_parameter",
                                              "reasoning.effort"))
    _w, rec = run(record, effort="medium")
    assert [b.get("reasoning") for b in bodies(api)] == [{"effort": "medium"}, None]
    assert rec.failed == ("error", "reasoning is not supported")


def test_web_search_refused_is_asked_again_without_it(api, record):
    api.on("POST", "/oa/responses",
           api_error(400, "Tool 'web_search' is not supported with this model.", "invalid_value", "tools"),
           oa(*text("ok")))
    w, rec = run(record, web=True)
    assert [{"type": "web_search"} in b.get("tools", []) for b in bodies(api)] == [True, False]
    assert "without web search" in w.note and rec.done == ("ok", [])


def test_chatgpt_sign_in_is_renewed_once_on_401(api, record, chatgpt):
    api.on("POST", "/oa/responses", Json(401, {"detail": "expired"}), oa(*text("ok")))
    _w, rec = run(record, auth="chatgpt")
    assert [r.header("authorization") for r in api.sent("/oa/responses")] == ["Bearer AT1", "Bearer AT1"]
    assert chatgpt == [False, True, False]
    assert rec.done == ("ok", [])
    api.on("POST", "/oa/responses", Json(401, {"detail": "expired"}))
    _w, rec = run(record, auth="chatgpt")
    assert rec.failed == ("auth", "expired")


def test_chatgpt_renewal_failure(api, record, monkeypatch):
    def token(creds, force=False):
        if force:
            raise chatgpt_signin.SignInError("The ChatGPT sign-in ran out")
        return "AT1"
    monkeypatch.setattr(chatgpt_signin, "access_token", token)
    api.on("POST", "/oa/responses", Json(401, {"detail": "expired"}))
    _w, rec = run(record, auth="chatgpt")
    assert rec.failed == ("auth", "The ChatGPT sign-in ran out")


def test_missing_key_fails_before_any_request(api, record):
    _w, rec = run(record, creds=MemoryCredentials({}))
    assert rec.events == [("failed", "auth", "No OpenAI API key saved.")] and api.requests == []


@pytest.fixture
def offline(monkeypatch):
    def unreachable(*a: Any, **kw: Any) -> Any:
        raise requests.exceptions.ConnectionError("Failed to establish a new connection")
    monkeypatch.setattr(OC.requests, "post", unreachable)
    monkeypatch.setattr(OC.requests, "get", unreachable)


def test_offline(record, offline):
    _w, rec = run(record)
    assert rec.events == [("failed", "ext_offline", "Failed to establish a new connection")]


@pytest.mark.parametrize("exc, kind", [(requests.exceptions.ReadTimeout("slow"), "timeout"),
                                       (RuntimeError("boom"), "error")])
def test_timeout_and_other_failures(record, monkeypatch, exc, kind):
    def post(*a: Any, **kw: Any) -> Any:
        raise exc
    monkeypatch.setattr(OC.requests, "post", post)
    _w, rec = run(record)
    assert rec.failed[0] == kind


def test_cancelled_before_start_sends_nothing(api, record):
    w = OC.OpenAIChat(ChatRequest("gpt-6.1", ASK, None, True, "high", MemoryCredentials({"openai_key": "k"}), "key"),
                      RecordingEvents())
    rec = record(w)
    w.cancel()
    w.run()
    assert rec.events == [] and api.requests == []


def test_cancel_closes_the_response(record):
    w = OC.OpenAIChat(ChatRequest("gpt-6.1", ASK, None, True, "high", MemoryCredentials({"openai_key": "k"}), "key"),
                      RecordingEvents())

    class Resp:
        closed = False

        def close(self) -> None:
            Resp.closed = True
    w._resp = Resp()
    w.cancel()
    assert Resp.closed and w._cancel


# ------------------------------------------------------------------ the model list
def test_list_models_with_a_key(api):
    api.on("GET", "/oa/models", Json(200, {"data": [
        {"id": "gpt-6.1", "created": 2}, {"id": "gpt-6.1-tts", "created": 3}, {"id": "text-embedding-4", "created": 4},
        {"id": "o5-mini", "created": 1}, {"id": "codex-mini", "created": 9}, {"id": "dall-e-4", "created": 5},
        {"id": "chatgpt-4o-latest"}, {"created": 7}, "junk"]}))
    assert OC.list_models(MemoryCredentials({"openai_key": "sk"}), "key") == [
        {"id": "codex-mini", "name": "codex-mini"}, {"id": "gpt-6.1", "name": "gpt-6.1"},
        {"id": "o5-mini", "name": "o5-mini"}, {"id": "chatgpt-4o-latest", "name": "chatgpt-4o-latest"}]
    assert api.sent("/oa/models")[0].header("authorization") == "Bearer sk"


def test_list_models_with_the_chatgpt_sign_in(api, chatgpt):
    api.on("GET", "/oa/models", Json(401, {"detail": "expired"}), Json(200, {"models": [
        {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list", "context_window": 272_000,
         "max_context_window": 1_050_000},
        {"slug": "gpt-6.1-mini", "context_window": 128_000},
        {"id": "by-id", "display_name": "By Id"},
        {"slug": "hidden", "display_name": "x", "visibility": "hide"},
        {"display_name": "no id"}, "junk"]}))
    assert OC.list_models(MemoryCredentials({}), "chatgpt") == [
        {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "context": 1_050_000},
        {"id": "gpt-6.1-mini", "name": "gpt-6.1-mini", "context": 128_000},
        {"id": "by-id", "name": "By Id"}]
    assert chatgpt == [False, True]


@pytest.mark.parametrize("response, kind, message", [
    (Json(401, {"error": {"message": "Incorrect API key"}}), "auth", "Incorrect API key"),
    (Json(500, {"error": {"code": "server_error"}}), "error", "server_error"),
    (Raw(503, b"down"), "error", "HTTP 503"),
    (Json(400, {"error": "flat"}), "error", "flat"),
])
def test_list_models_errors(api, response, kind, message):
    api.on("GET", "/oa/models", response)
    with pytest.raises(OC.OpenAIError) as e:
        OC.list_models(MemoryCredentials({"openai_key": "sk"}), "key")
    assert (e.value.kind, str(e.value)) == (kind, message)


def test_list_models_offline(offline):
    with pytest.raises(OC.OpenAIError) as e:
        OC.list_models(MemoryCredentials({"openai_key": "sk"}), "key")
    assert e.value.kind == "ext_offline"


def test_list_models_needs_credentials():
    with pytest.raises(OC.OpenAIError) as e:
        OC.list_models(MemoryCredentials({}), "key")
    assert e.value.kind == "auth"


def test_list_models_odd_json(api):
    api.on("GET", "/oa/models", Json(200, ["not", "a", "dict"]))
    assert OC.list_models(MemoryCredentials({"openai_key": "sk"}), "key") == []
