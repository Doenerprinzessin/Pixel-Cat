"""Fakes for the AI provider tests: a scripted HTTP server on 127.0.0.1 and a signal recorder.

``FakeHTTP`` plays the providers' APIs (JSON, server-sent events, NDJSON) and records every
request.  Routes are ``(method, path)``; each holds a list of responses - one is used per
request, the last one repeats.  A callable response gets the Request and returns one.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


# ------------------------------------------------------------------ responses
@dataclass
class Json:
    status: int = 200
    body: Any = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SSE:
    """Server-sent events; ``named``: an ``event:`` line per event (Anthropic's style)."""
    events: list[dict[str, Any]]
    named: bool = False
    done: bool = False                        # end with ``data: [DONE]``


@dataclass
class Lines:
    """Newline-delimited JSON (Ollama's streaming); bytes items are sent as they are."""
    lines: list[Any]
    status: int = 200


@dataclass
class Raw:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    @property
    def route(self) -> str:
        return urlparse(self.path).path

    @property
    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def json(self) -> Any:
        return json.loads(self.body or b"null")

    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode()).items()}

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


_SPECS = (Json, SSE, Lines, Raw)


class FakeHTTP:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Any]] = {}
        self.requests: list[Request] = []
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                pass

            def _handle(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                req = Request(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                with fake._lock:
                    fake.requests.append(req)
                    queue = fake.routes.get((self.command, req.route))
                    spec = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else Json(404, {"error": "no route"})
                while callable(spec) and not isinstance(spec, _SPECS):
                    spec = spec(req)
                fake._send(self, spec)

            do_GET = do_POST = _handle  # noqa: N815

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request: Any, client_address: Any) -> None:
                pass                                # (a client that hung up early)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def on(self, method: str, path: str, *responses: Any) -> None:
        self.routes[(method, path)] = list(responses)

    def sent(self, path: str | None = None) -> list[Request]:
        return [r for r in self.requests if path is None or r.route == path]

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, spec: Any) -> None:
        if isinstance(spec, Json):
            data = json.dumps(spec.body).encode()
            h.send_response(spec.status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            for k, v in spec.headers.items():
                h.send_header(k, v)
            h.end_headers()
            h.wfile.write(data)
        elif isinstance(spec, SSE):
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream")
            h.end_headers()
            for ev in spec.events:
                line = (f"event: {ev['type']}\n" if spec.named else "") + f"data: {json.dumps(ev)}\n\n"
                h.wfile.write(line.encode())
            if spec.done:
                h.wfile.write(b"data: [DONE]\n\n")
            h.wfile.flush()
        elif isinstance(spec, Lines):
            data = b"".join((ln if isinstance(ln, bytes) else json.dumps(ln).encode()) + b"\n" for ln in spec.lines)
            h.send_response(spec.status)
            h.send_header("Content-Type", "application/x-ndjson")
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
        else:
            h.send_response(spec.status)
            for k, v in spec.headers.items():
                h.send_header(k, v)
            h.send_header("Content-Length", str(len(spec.body)))
            h.end_headers()
            h.wfile.write(spec.body)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ------------------------------------------------------------------ worker signals
class Recorder:
    """Connects to every signal a worker has; ``events``: (signal, *args) in emit order."""

    SIGNALS = ("token", "thinking", "status", "used", "credits", "permission", "done", "failed")

    def __init__(self, worker: Any) -> None:
        self.events: list[tuple] = []
        self._lock = threading.Lock()
        for name in self.SIGNALS:
            sig = getattr(worker, name, None)
            if sig is not None:
                sig.connect(self._slot(name))

    def _slot(self, name: str) -> Callable[..., None]:
        def slot(*args: Any) -> None:
            with self._lock:
                self.events.append((name, *args))
        return slot

    def of(self, name: str) -> list[tuple]:
        return [e[1:] for e in self.events if e[0] == name]

    @property
    def tokens(self) -> str:
        return "".join(a[0] for a in self.of("token"))

    @property
    def done(self) -> tuple | None:
        d = self.of("done")
        return d[-1] if d else None

    @property
    def failed(self) -> tuple | None:
        f = self.of("failed")
        return f[-1] if f else None

    @property
    def kinds(self) -> list[str]:
        return [e[0] for e in self.events]

    def limit(self) -> dict[str, Any]:
        assert self.failed is not None and self.failed[0] == "limit", self.events
        return json.loads(self.failed[1])


# ------------------------------------------------------------------ a chat history every provider converts
PNG = "iVBORw0KGgoAAA"
JPEG = "/9j/4AAQSk"
HISTORY: list[dict[str, Any]] = [
    {"role": "system", "content": "You are the cat."},
    {"role": "user", "content": "open notepad and look", "images": [PNG]},
    {"role": "assistant", "content": "On it.", "tool_calls": [
        {"function": {"name": "open_app", "arguments": {"name": "notepad"}}},
        {"function": {"name": "take_screenshot", "arguments": {}}}]},
    {"role": "tool", "tool_name": "open_app", "content": "opened Notepad"},
    {"role": "tool", "tool_name": "take_screenshot", "content": "here it is", "images": [JPEG]},
    {"role": "user", "content": "(note from the app)", "_tool": True},
    {"role": "assistant", "content": "Typing.",
     "tool_calls": [{"id": "toolu_x", "function": {"name": "type_text", "arguments": {"text": "hi"}}}],
     "raw": [{"type": "thinking", "thinking": "hmm", "signature": "S"}, {"type": "text", "text": "Typing."},
             {"type": "tool_use", "id": "toolu_x", "name": "type_text", "input": {"text": "hi"}}]},
    {"role": "tool", "tool_name": "type_text", "content": "typed"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "press_keys",
                                                                      "arguments": {"keys": "x"}}}]},
    {"role": "user", "content": "stop that, do something else"},
]
TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "open_app", "description": "Open an app",
                                      "parameters": {"type": "object",
                                                     "properties": {"name": {"type": "string"}}}}},
    {"name": "get_time"},
]
