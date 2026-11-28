"""ollama_client: the streaming OllamaChat against a fake Ollama on 127.0.0.1, text tool calls,
hiding tool markup, preloading and the cat's error messages."""
from __future__ import annotations

import logging

import pytest

from fakes.chat import RecordingEvents
from adapters.llm.fakes import Json, Lines, Raw
from pixelcat.adapters.llm import ollama_client as OL
from pixelcat.ports.llm import ChatRequest
from pixelcat.settings import Config

TOOLS = [{"type": "function", "function": {"name": "open_app", "parameters": {"type": "object"}}}]
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi", "images": ["iVBOR"]},
        {"role": "assistant", "content": "", "raw": [{"type": "text"}],
         "tool_calls": [{"id": "x", "_id": "x", "function": {"name": "open_app", "arguments": {"name": "a"}}}]},
        {"role": "tool", "content": "ok", "tool_name": "open_app"}]


def chunk(content: str = "", thinking: str = "", calls: list | None = None, done: bool = False, **extra) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if calls is not None:
        msg["tool_calls"] = calls
    return {"model": "m", "message": msg, "done": done, **extra}


@pytest.fixture
def ollama(fake_http):
    return fake_http


@pytest.fixture
def cfg(ollama) -> Config:
    return Config(ollama_url=ollama.url + "/", num_ctx=4096, think_max_tokens=900, keep_alive="5m", temperature=0.3)


def payloads(ollama) -> list[dict]:
    return [r.json() for r in ollama.sent("/api/chat")]


def run(record, cfg: Config, think=None, model=None, tools=TOOLS, msgs=MSGS):
    w = OL.OllamaChat(ChatRequest(model or "", msgs, tools, think, cfg=cfg), RecordingEvents())
    rec = record(w)
    w.run()
    return w, rec


# ------------------------------------------------------------------ the worker
def test_stream_thinking_text_and_tool_calls(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([
        chunk(thinking="Hmm, "), chunk(thinking="ok."), chunk("Hello "), b"", chunk("cat!"),
        chunk(calls=[{"function": {"name": "open_app", "arguments": {"name": "Paint"}}},
                     {"function": {"name": "a", "arguments": '{"k": 1}'}},
                     {"function": {"name": "b", "arguments": "not json"}},
                     {"function": {"name": "c"}}, {}]),
        chunk(done=True, prompt_eval_count=100, eval_count=5, prompt_eval_duration=1e9)]))
    _w, rec = run(record, cfg)
    assert rec.events == [
        ("thinking", 5), ("thinking", 8), ("token", "Hello "), ("token", "cat!"),
        ("done", "Hello cat!", [{"name": "open_app", "arguments": {"name": "Paint"}},
                                {"name": "a", "arguments": {"k": 1}}, {"name": "b", "arguments": {}},
                                {"name": "c", "arguments": {}}, {"name": "", "arguments": {}}])]


def test_payload(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([chunk("x", done=True)]))
    run(record, cfg, model="qwen3.5:9b")
    assert payloads(ollama)[0] == {
        "model": "qwen3.5:9b", "stream": True, "keep_alive": "5m", "tools": TOOLS,
        "options": {"temperature": 0.3, "num_ctx": 4096},
        "messages": [MSGS[0], MSGS[1],
                     {"role": "assistant", "content": "",
                      "tool_calls": [{"function": {"name": "open_app", "arguments": {"name": "a"}}}]},
                     MSGS[3]]}


@pytest.mark.parametrize("think, sent, predict", [(None, None, None), (True, True, 900), (False, False, None)])
def test_think_flag(ollama, cfg, record, think, sent, predict):
    ollama.on("POST", "/api/chat", Lines([chunk("x", done=True)]))
    w, _rec = run(record, cfg, think=think, tools=None)
    p = payloads(ollama)[0]
    assert p.get("think") == sent and p["options"].get("num_predict") == predict and "tools" not in p
    assert w.model == cfg.model


@pytest.mark.parametrize("status, body, kind, detail", [
    (404, b'{"error":"model \\"x\\" not found, try pulling it first"}', "model_missing", None),
    (400, b'{"error":"registry: not found"}', "model_missing", None),
    (400, b'{"error":"x does not support tools"}', "no_tools", None),
    (500, b'{"error":"boom"}', "server", 'HTTP 500: {"error":"boom"}'),
    (400, b'{"error":"x does not support thinking"}', "server",
     'HTTP 400: {"error":"x does not support thinking"}'),                   # (no think flag was sent)
])
def test_http_errors(ollama, cfg, record, status, body, kind, detail):
    ollama.on("POST", "/api/chat", Raw(status, body))
    _w, rec = run(record, cfg)
    assert rec.events == [("failed", kind, detail or body.decode())]


def test_a_model_that_cant_think_is_asked_again_without(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Raw(400, b'{"error":"m does not support thinking"}'), Lines([chunk("ok", done=True)]))
    _w, rec = run(record, cfg, think=True)
    assert [p.get("think") for p in payloads(ollama)] == [True, False]
    assert rec.done == ("ok", [])


def test_thinking_that_used_up_the_answer_is_asked_again(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([chunk(thinking="long thoughts"), chunk(done=True)]),
              Lines([chunk("short", done=True)]))
    _w, rec = run(record, cfg, think=True)
    assert [p.get("think") for p in payloads(ollama)] == [True, False]
    assert rec.events == [("thinking", 13), ("token", "short"), ("done", "short", [])]


@pytest.mark.parametrize("err", ["EOF", "error parsing tool call"])
def test_a_broken_tool_call_is_asked_again_once(ollama, cfg, record, err):
    ollama.on("POST", "/api/chat", Lines([{"error": err}]), Lines([chunk("fine", done=True)]))
    _w, rec = run(record, cfg)
    assert len(payloads(ollama)) == 2 and rec.done == ("fine", [])


def test_two_broken_answers_end_without_an_event(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([{"error": "EOF"}]))
    _w, rec = run(record, cfg)
    assert len(payloads(ollama)) == 2
    assert rec.events == []                      # current behaviour: neither done nor failed


@pytest.mark.parametrize("lines, kind, detail", [
    ([chunk("partial"), {"error": "EOF"}], "server", "EOF"),
    ([{"error": "model does not support tools"}], "no_tools", "model does not support tools"),
    ([{"error": "out of memory"}], "server", "out of memory"),
])
def test_error_lines(ollama, cfg, record, lines, kind, detail):
    ollama.on("POST", "/api/chat", Lines(lines))
    _w, rec = run(record, cfg)
    assert rec.failed == (kind, detail) and len(payloads(ollama)) == 1


def test_full_context_is_logged(ollama, cfg, record, caplog):
    ollama.on("POST", "/api/chat", Lines([chunk("x", done=True, prompt_eval_count=4090)]))
    with caplog.at_level(logging.WARNING, logger=OL.__name__):
        _w, rec = run(record, cfg)
    assert rec.done == ("x", []) and "filled the whole context" in caplog.text


def test_offline(record, monkeypatch):
    def post(*a, **kw):
        raise OL.requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(OL.requests, "post", post)
    _w, rec = run(record, Config(ollama_url="http://localhost:11434/"))
    assert rec.events == [("failed", "offline", "http://localhost:11434/api/chat")]


@pytest.mark.parametrize("exc, kind, detail", [
    (OL.requests.exceptions.ReadTimeout("slow"), "timeout", "120.0"),
    (RuntimeError("boom"), "error", "boom"),
])
def test_timeout_and_other_failures(record, monkeypatch, exc, kind, detail):
    def post(*a, **kw):
        raise exc
    monkeypatch.setattr(OL.requests, "post", post)
    _w, rec = run(record, Config())
    assert rec.events == [("failed", kind, detail)]


def test_bad_json_line_is_an_error(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([b"{not json"]))
    _w, rec = run(record, cfg)
    assert rec.failed[0] == "error"


def test_cancel_mid_stream_still_reports_what_came(ollama, cfg, record):
    ollama.on("POST", "/api/chat", Lines([chunk("a"), chunk("b"), chunk("c", done=True)]))
    w = OL.OllamaChat(ChatRequest("", MSGS, None, cfg=cfg), RecordingEvents(on={"token": lambda _t: w.cancel()}))
    rec = record(w)
    w.run()
    assert rec.events == [("token", "a"), ("done", "a", [])]       # current behaviour: done after a cancel


def test_cancel_closes_the_response():
    w = OL.OllamaChat(ChatRequest("", [], None, cfg=Config()), RecordingEvents())

    class Resp:
        closed = False

        def close(self) -> None:
            Resp.closed = True
    w._resp = Resp()
    w.cancel()
    assert Resp.closed and w._cancel


def test_preload_model(ollama):
    import threading

    got = threading.Event()
    ollama.on("POST", "/api/generate", lambda req: (got.set(), Json(200, {}))[1])
    cfg = Config(ollama_url=ollama.url, model="qwen3.5:4b", keep_alive="7m", num_ctx=8192)
    OL.preload_model(cfg)
    assert got.wait(5)
    assert ollama.sent("/api/generate")[0].json() == {"model": "qwen3.5:4b", "prompt": "", "keep_alive": "7m",
                                                      "options": {"num_ctx": 8192}}


def test_preload_model_failure_is_quiet(monkeypatch):
    import threading

    done = threading.Event()

    def post(*a, **kw):
        done.set()
        raise OSError("down")
    monkeypatch.setattr(OL.requests, "post", post)
    OL.preload_model(Config())
    assert done.wait(5)


# ------------------------------------------------------------------ tool calls written as text
@pytest.mark.parametrize("text, known, calls", [
    ('<tool_call>{"name": "open_app", "arguments": {"name": "Paint"}}</tool_call>', None,
     [{"name": "open_app", "arguments": {"name": "Paint"}}]),
    ('Sure!\n```json\n{"name": "a", "arguments": {}}\n{"tool": "b", "args": {"x": 1}}\n```', None,
     [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {"x": 1}}]),
    ('{"function": {"name": "c", "arguments": "{\\"y\\": 2}"}}', None, [{"name": "c", "arguments": {"y": 2}}]),
    ('{"name": "d", "parameters": {"z": 3}}', None, [{"name": "d", "arguments": {"z": 3}}]),
    ('{"name": "e"}', None, [{"name": "e", "arguments": {}}]),
    ('{"name": "f", "arguments": "not json"}', None, []),
    ('{"name": "g", "arguments": [1, 2]}', None, []),
    ('{"arguments": {}}', None, []),
    ('{"name": 5, "arguments": {}}', None, []),
    ('{"name": "open_app", "arguments": {}} {"name": "unknown", "arguments": {}}', {"open_app"},
     [{"name": "open_app", "arguments": {}}]),
    ('{"outer": {"name": "inner", "arguments": {}}}', None, []),
    ('broken { "name": "x", and then {"name": "ok", "arguments": {}}', None, [{"name": "ok", "arguments": {}}]),
    ("no json at all", None, []),
    ('[{"name": "in_a_list", "arguments": {}}]', None, [{"name": "in_a_list", "arguments": {}}]),
])
def test_parse_text_tool_calls(text, known, calls):
    assert OL.parse_text_tool_calls(text, known) == calls


@pytest.mark.parametrize("text, known, visible", [
    ('Opening it.<tool_call>{"name": "open_app", "arguments": {}}</tool_call>', None, "Opening it."),
    ('Here:\n```json\n{"name": "a", "arguments": {}}\n```\nDone.', None, "Here:\n\nDone."),
    ('```\n{"name": "a", "arguments": {}}\n```', None, ""),
    ('Data: {"temperature": 20}', None, 'Data: {"temperature": 20}'),
    ('{"name": "unknown", "arguments": {}} keep', {"open_app"}, '{"name": "unknown", "arguments": {}} keep'),
    ("  <tool_call>  ", None, ""),
    ('half {"name": "open_app", "argu', None, 'half {"name": "open_app", "argu'),
])
def test_strip_tool_markup(text, known, visible):
    assert OL.strip_tool_markup(text, known) == visible


# ------------------------------------------------------------------ messages
@pytest.mark.parametrize("kind, needle", [
    ("offline", "Ollama doesn't seem to be running at http://h:1"),
    ("model_missing", "ollama pull qwen3.5:9b"),
    ("timeout", "timed out"),
    ("no_tools", "can't use tools natively"),
    ("server", "Hiss… something went wrong 😾\n```text\nboom\n```"),
])
def test_friendly_error(kind, needle):
    assert needle in OL.friendly_error(kind, "boom", "qwen3.5:9b", "http://h:1")


def test_friendly_error_clips_the_detail():
    assert "x" * 300 in OL.friendly_error("error", "x" * 500, "m", "u")
    assert "x" * 301 not in OL.friendly_error("error", "x" * 500, "m", "u")
