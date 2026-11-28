"""External AI models test (developer tool).  Nothing goes to Anthropic or OpenAI: a local
fake server plays their APIs and OpenAI's sign-in.  APPDATA is moved to build/externaltest,
so the real keys and save are never touched.

* Token limits: limit -> raise for now (up to the maximum) -> all used; resets per period.
* keys.dat is encrypted; a key that is only being checked is never written.
* The cat's messages as Claude / Responses input: tool calls paired with results by id,
  screenshots, the model's own blocks replayed, calls that never ran.
* Claude (official SDK): text, thinking, tool calls, usage; settings a model refuses step
  down (thinking off: disabled -> between_tools); rate limit -> its reset; credits; refusal.
* OpenAI (Responses API): text, tool calls, usage; reasoning.effort refused -> asked again;
  the ChatGPT plan's limit; a ChatGPT sign-in that expired is renewed.
* Sign in with ChatGPT: PKCE, dynamic client, the ID token's signature, the next sign-in.
* In the app: the menu; limit reached -> SWITCH / RAISE / STOP; all used -> popup with the
  reset; a provider's rate limit -> popup; a key checked in the key dialog.
Usage:  python tools/external_test.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "build" / "externaltest"
shutil.rmtree(TEST, ignore_errors=True)
(TEST / "AppData").mkdir(parents=True)
os.environ["APPDATA"] = str(TEST / "AppData")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE"):
    os.environ.pop(k, None)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelcat.bootstrap import build_platform  # noqa: E402
from pixelcat.adapters.llm import anthropic_client as AC  # noqa: E402
from pixelcat.adapters.llm import chatgpt_signin as CS  # noqa: E402
from pixelcat.llm import external as X  # noqa: E402
from pixelcat.adapters.llm import openai_client as OC  # noqa: E402
from pixelcat.llm.qt_chat import QtChatWorker  # noqa: E402
from pixelcat.ports.llm import ChatRequest  # noqa: E402

SECRETS = build_platform().secrets                 # keys.dat, as the app keeps it

app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


# ------------------------------------------------------------------ a throwaway RSA key (signs ID tokens)
def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(24):
        x = pow(random.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int) -> int:
    while True:
        c = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(c):
            return c


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


E = 65537
while True:
    P, Q = _prime(512), _prime(512)
    PHI = (P - 1) * (Q - 1)
    if PHI % E:
        break
N, D = P * Q, pow(E, -1, (P - 1) * (Q - 1))
JWK = {"kty": "RSA", "kid": "k1", "n": b64u(N.to_bytes((N.bit_length() + 7) // 8, "big")),
       "e": b64u(E.to_bytes(3, "big"))}


def jwt(claims: dict, bad: bool = False) -> str:
    h = b64u(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
    b = b64u(json.dumps(claims).encode())
    size = (N.bit_length() + 7) // 8
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{h}.{b}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    sig = pow(int.from_bytes(em, "big"), D, N) + (1 if bad else 0)
    return f"{h}.{b}.{b64u(sig.to_bytes(size, 'big'))}"


# ------------------------------------------------------------------ the fake server
S: dict = {"bodies": [], "headers": [], "mode": "text", "oa_mode": "text", "authorize": [], "token": [],
           "revoked": [], "refresh_ok": True, "bad_sig": False}


def key_of(headers) -> str:
    """The API key a request carried (the SDK sends it as a bearer token or as x-api-key)."""
    get = headers.get
    auth = get("Authorization") or get("authorization") or ""
    return get("x-api-key") or get("X-Api-Key") or (auth[7:] if auth.startswith("Bearer ") else "")


def sse(h: BaseHTTPRequestHandler, events: list[dict], named: bool = True) -> None:
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    for ev in events:
        line = (f"event: {ev['type']}\n" if named else "") + f"data: {json.dumps(ev)}\n\n"
        h.wfile.write(line.encode())
        h.wfile.flush()


def jsend(h: BaseHTTPRequestHandler, code: int, obj: dict, headers: dict | None = None) -> None:
    data = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(data)


def msg_start(usage: dict | None = None) -> dict:
    return {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "content": [], "model": "claude-opus-5-5",
        "stop_reason": None, "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 50,
                           "cache_creation_input_tokens": 0}}}


def claude_events(mode: str) -> list[dict]:
    ev = [msg_start()]
    if mode in ("search", "pause"):                      # Anthropic's web search: the search, results, a cited answer
        ev += [{"type": "content_block_start", "index": 0, "content_block": {
                    "type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}}},
               {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                                                                     "partial_json": '{"query": "cat news"}'}},
               {"type": "content_block_stop", "index": 0}]
        if mode == "search":
            ev += [{"type": "content_block_start", "index": 1, "content_block": {
                        "type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
                            {"type": "web_search_result", "url": "https://news.example.org/cats", "title": "Cat News",
                             "encrypted_content": "E1", "page_age": None}]}},
                   {"type": "content_block_stop", "index": 1},
                   {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": "",
                                                                                "citations": []}},
                   {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta",
                                                                         "text": "Cats are napping."}},
                   {"type": "content_block_delta", "index": 2, "delta": {"type": "citations_delta", "citation": {
                       "type": "web_search_result_location", "url": "https://news.example.org/cats",
                       "title": "Cat News", "encrypted_index": "I1", "cited_text": "Cats nap"}}},
                   {"type": "content_block_stop", "index": 2}]
    if mode in ("text", "tool"):
        ev += [{"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "",
                                                                            "signature": ""}},
               {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me see"}},
               {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG1"}},
               {"type": "content_block_stop", "index": 0},
               {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello "}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "there!"}},
               {"type": "content_block_stop", "index": 1}]
    if mode == "tool":
        ev += [{"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_7",
                                                                            "name": "open_app", "input": {}}},
               {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta",
                                                                     "partial_json": '{"name": "Note'}},
               {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'pad"}'}},
               {"type": "content_block_stop", "index": 2}]
    stop = {"tool": "tool_use", "refusal": "refusal", "pause": "pause_turn"}.get(mode, "end_turn")
    if mode == "refusal":
        ev += [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
               {"type": "content_block_stop", "index": 0}]
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 450}},
           {"type": "message_stop"}]
    return ev


def oa_events(mode: str) -> list[dict]:
    ev = [{"type": "response.created", "response": {"id": "resp_1"}},
          {"type": "response.output_item.added", "item": {"type": "reasoning", "id": "rs_1"}}]
    output: list[dict] = []
    if mode == "search":                                 # OpenAI's web search: the call, a cited answer
        ev += [{"type": "response.output_item.added", "item": {"type": "web_search_call", "id": "ws_1",
                                                               "status": "in_progress"}},
               {"type": "response.output_text.delta", "delta": "Cats rule ([Cat Daily](https://daily.example.com/x))."}]
        output = [{"type": "message", "content": [{"type": "output_text", "text": "Cats rule.", "annotations": [
            {"type": "url_citation", "url": "https://daily.example.com/x", "title": "Cat Daily"},
            {"type": "url_citation", "url": "https://wiki.example.org/Cat", "title": "Cat - Wiki"}]}]}]
    else:
        ev += [{"type": "response.output_text.delta", "delta": "Hi "},
               {"type": "response.output_text.delta", "delta": "from GPT"}]
    if mode == "tool":
        ev.append({"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1",
                                                                  "call_id": "call_9", "name": "open_app",
                                                                  "arguments": '{"name": "Paint"}'}})
    ev.append({"type": "response.completed", "response": {"id": "resp_1", "output": output,
                                                          "usage": {"input_tokens": 300, "output_tokens": 40,
                                                                    "total_tokens": 340}}})
    return ev


PAGE = ("<html><head><title>Cat Facts</title><script>alert('x')</script><style>p{}</style></head><body>"
        "<nav><a href='/'>Home</a></nav><h1>Cats</h1><p>Cats sleep a lot &amp; purr.</p>"
        "<ul><li>naps</li><li>zoomies</li></ul><p>Read <a href='/web/more'>more</a>.</p></body></html>")


class H(BaseHTTPRequestHandler):
    def log_message(self, *_a) -> None:
        pass

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path.startswith("/web/"):                 # web pages for read_web_page
            if url.path == "/web/redirect":
                self.send_response(302)
                self.send_header("Location", "/web/page")
                self.end_headers()
                return
            kind, body = {"/web/page": ("text/html; charset=utf-8", PAGE.encode()),
                          "/web/long": ("text/plain", ("purr " * 6000).encode()),
                          "/web/img": ("image/png", b"\x89PNG....")}.get(url.path, ("text/plain", b""))
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/v1/models":
            S["headers"].append(dict(self.headers))
            if key_of(self.headers) == "sk-ant-bad":
                return jsend(self, 401, {"type": "error", "error": {"type": "authentication_error",
                                                                    "message": "invalid x-api-key"}})
            return jsend(self, 200, {"data": [
                {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
                 "created_at": "2026-09-01T00:00:00Z", "max_input_tokens": 1000000, "max_tokens": 128000,
                 "capabilities": {"thinking": {"supported": True, "types": {"adaptive": {"supported": True},
                                                                            "enabled": {"supported": False}}},
                                  "effort": {"supported": True, **{e: {"supported": True} for e in
                                                                   ("low", "medium", "high", "xhigh", "max")}},
                                  "image_input": {"supported": True}}},
                {"type": "model", "id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5",
                 "created_at": "2026-09-01T00:00:00Z", "max_input_tokens": 1000000, "max_tokens": 128000,
                 "capabilities": {}}], "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-haiku-5-5"})
        if url.path == "/oa/models":
            if "chatgpt" in self.headers.get("Authorization", ""):
                return jsend(self, 200, {"models": [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol",
                                                     "visibility": "list", "context_window": 272000,
                                                     "max_context_window": 1050000},
                                                    {"slug": "hidden", "display_name": "x", "visibility": "hide"}]})
            return jsend(self, 200, {"data": [{"id": "gpt-6.1", "created": 2}, {"id": "gpt-6.1-tts", "created": 3},
                                              {"id": "text-embedding-4", "created": 4}, {"id": "o5-mini", "created": 1}]})
        if url.path == "/auth/.well-known/openid-configuration":
            base = f"http://127.0.0.1:{SRV.server_address[1]}/auth"
            return jsend(self, 200, {"issuer": base, "jwks_uri": base + "/jwks", "revocation_endpoint": base + "/revoke"})
        if url.path == "/auth/jwks":
            return jsend(self, 200, {"keys": [JWK]})
        if url.path == "/auth/authorize":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            S["authorize"].append(q)
            if S.get("error_page"):                    # (an error page that never comes back to the app)
                return jsend(self, 200, {"error_code": "3p_login_workspace_scope_denied"})
            back = {"code": "CODE1", "state": q["state"], "scope": q["scope"]}
            if q["client_id"] == "dynamic_agent_client":
                back["client_id"] = "oaiapp_123"
            self.send_response(302)
            self.send_header("Location", q["redirect_uri"] + "?" + urlencode(back))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        raw = self._body()
        if url.path.startswith("/v1/messages"):
            body = json.loads(raw)
            S["bodies"].append(body)
            S["headers"].append(dict(self.headers))
            mode = S["mode"]
            if mode == "off_once" and body.get("thinking", {}).get("type") == "disabled":
                return jsend(self, 400, {"type": "error", "error": {
                    "type": "invalid_request_error",
                    "message": "thinking.type: 'disabled' is not supported for this model"}})
            if mode == "rate":
                reset = (datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                return jsend(self, 429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
                             {"anthropic-ratelimit-tokens-remaining": "0", "anthropic-ratelimit-tokens-reset": reset,
                              "x-should-retry": "false"})
            if mode == "credits":
                return jsend(self, 400, {"type": "error", "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API."}})
            if mode in ("too_long", "too_long_once"):
                if mode == "too_long_once":
                    S["mode"] = "text"
                return jsend(self, 400, {"type": "error", "error": {
                    "type": "invalid_request_error", "message": "prompt is too long: 250000 tokens > 200000 maximum"}})
            if mode == "search_off" and any(str(t.get("type", "")).startswith("web_search") for t in body.get("tools", [])):
                return jsend(self, 400, {"type": "error", "error": {
                    "type": "invalid_request_error", "message": "Web search is not enabled for this organization."}})
            if mode == "pause_once":
                S["mode"] = "search"
                return sse(self, claude_events("pause"))
            return sse(self, claude_events({"off_once": "text", "search_off": "text"}.get(mode, mode)))
        if url.path == "/oa/responses":
            body = json.loads(raw)
            S["bodies"].append(body)
            S["headers"].append(dict(self.headers))
            mode = S["oa_mode"]
            if mode == "no_effort" and "reasoning" in body:
                return jsend(self, 400, {"error": {"message": "Unsupported parameter: 'reasoning.effort'",
                                                   "type": "invalid_request_error", "param": "reasoning.effort",
                                                   "code": "unsupported_parameter"}})
            if mode == "plan":
                return jsend(self, 429, {"error": {"message": "limit", "code": "subscription_sharing_usage_limit_exceeded"}})
            if mode == "too_long":
                return jsend(self, 400, {"error": {"message": "Your input exceeds the context window of this model. "
                                                              "Please adjust your input and try again.",
                                                   "type": "invalid_request_error", "param": "input",
                                                   "code": "context_length_exceeded"}})
            if mode == "no_search" and {"type": "web_search"} in body.get("tools", []):
                return jsend(self, 400, {"error": {"message": "Tool 'web_search' is not supported with this model.",
                                                   "type": "invalid_request_error", "param": "tools",
                                                   "code": "invalid_value"}})
            if mode == "expired" and self.headers.get("Authorization") == "Bearer chatgpt-old":
                return jsend(self, 401, {"detail": "expired"})
            return sse(self, oa_events(mode if mode in ("tool", "search") else "text"), named=False)
        if url.path == "/auth/token":
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            S["token"].append(form)
            base = f"http://127.0.0.1:{SRV.server_address[1]}/auth"
            if form["grant_type"] == "refresh_token":
                if not S["refresh_ok"]:
                    return jsend(self, 400, {"error": "invalid_grant"})
                return jsend(self, 200, {"access_token": "chatgpt-new", "refresh_token": "R2", "expires_in": 3600})
            if S.get("refuse_codes", 0) > 0:          # (OpenAI not taking a fresh registration's first code)
                S["refuse_codes"] -= 1
                return jsend(self, 400, {"error": "invalid_grant", "error_description": "Invalid authorization code"})
            nonce = S["authorize"][-1]["nonce"]
            idt = jwt({"iss": base, "aud": form["client_id"], "exp": time.time() + 3600, "nonce": nonce,
                       "email": "cat@example.com", "sub": "u1"}, bad=S["bad_sig"])
            return jsend(self, 200, {"access_token": "chatgpt-AT", "refresh_token": "R1", "id_token": idt,
                                     "expires_in": 3600, "token_type": "Bearer",
                                     "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"})
        if url.path == "/auth/revoke":
            S["revoked"].append({k: v[0] for k, v in parse_qs(raw.decode()).items()})
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


SRV = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
X.BASE_URLS["anthropic"] = BASE
X.BASE_URLS["openai"] = BASE + "/oa"
CS.URLS.update(issuer=BASE + "/auth", authorize=BASE + "/auth/authorize", token=BASE + "/auth/token",
               discovery=BASE + "/auth/.well-known/openid-configuration")
CS.PORT = 0


def anthropic_worker(model, messages, tools, think, effort, creds, info=None, auth="key") -> QtChatWorker:
    req = ChatRequest(model, messages, tools, think, effort, creds, auth, info or {})
    return QtChatWorker(lambda events: AC.AnthropicChat(req, events), provider="anthropic", model=model, local=False)


def openai_worker(model, messages, tools, think, effort, creds, auth="key") -> QtChatWorker:
    req = ChatRequest(model, messages, tools, think, effort, creds, auth)
    return QtChatWorker(lambda events: OC.OpenAIChat(req, events), provider="openai", model=model, local=False)


def run_worker(w, timeout: float = 20.0, web: bool = False) -> dict:
    out: dict = {"tokens": [], "thinking": 0, "done": None, "failed": None, "used": [], "status": []}
    w.job.web = web
    w.status.connect(lambda s: out["status"].append(s))
    w.token.connect(lambda t: out["tokens"].append(t))
    w.thinking.connect(lambda n: out.__setitem__("thinking", n))
    w.done.connect(lambda c, calls: out.__setitem__("done", (c, calls)))
    w.failed.connect(lambda k, d: out.__setitem__("failed", (k, d)))
    w.used.connect(lambda p, n: out["used"].append((p, n)))
    w.start()
    end = time.time() + timeout
    while (out["done"] is None and out["failed"] is None) and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    w.wait(5000)
    app.processEvents()
    return out


HISTORY = [
    {"role": "system", "content": "You are the cat."},
    {"role": "user", "content": "open notepad and look", "images": ["iVBORw0KGgoAAA"]},
    {"role": "assistant", "content": "On it.", "tool_calls": [
        {"function": {"name": "open_app", "arguments": {"name": "notepad"}}},
        {"function": {"name": "take_screenshot", "arguments": {}}}]},
    {"role": "tool", "tool_name": "open_app", "content": "opened Notepad"},
    {"role": "tool", "tool_name": "take_screenshot", "content": "here it is", "images": ["/9j/4AAQSk"]},
    {"role": "user", "content": "(note from the app)", "_tool": True},
    {"role": "assistant", "content": "Typing.", "tool_calls": [{"id": "toolu_x", "function": {"name": "type_text",
                                                                                            "arguments": {"text": "hi"}}}],
     "raw": [{"type": "thinking", "thinking": "hmm", "signature": "S"}, {"type": "text", "text": "Typing."},
             {"type": "tool_use", "id": "toolu_x", "name": "type_text", "input": {"text": "hi"}}]},
    {"role": "tool", "tool_name": "type_text", "content": "typed"},
    {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "press_keys", "arguments": {"keys": "x"}}}]},
    {"role": "user", "content": "stop that, do something else"},
]


def main() -> int:  # noqa: C901 - a linear test script
    # token limits ----------------------------------------------------------------------------
    s, u = {"limit": 1000, "period": "day", "maximum": 2.0}, {}
    b = X.Budget(s, u)
    t0 = datetime(2026, 11, 19, 15, 0)
    b.add(999, t0)
    st1 = b.status(t0)
    b.add(1, t0)
    st2, nxt = b.status(t0), b.next_allowed(t0)
    b.raise_limit(t0)
    st3 = b.status(t0)
    b.add(500, t0)
    nxt2 = b.next_allowed(t0)
    b.raise_limit(t0)
    b.add(500, t0)
    st4 = b.status(t0)
    t1 = t0 + timedelta(days=1)
    check("limit: ok -> reached (suggest) -> raised half a limit -> again -> up to the maximum -> all used",
          (st1, st2, nxt, st3, nxt2, st4) == ("ok", "soft", 1500, "ok", 2000, "hard"), str((st1, st2, nxt, st3, nxt2, st4)))
    check("...a new day starts from zero, without the raises", b.status(t1) == "ok" and b.used(t1) == 0
          and b.allowed(t1) == 1000)
    check("resets: day at midnight, week on Monday, month on the 1st",
          X.next_reset("day", t0) == datetime(2026, 11, 20) and X.next_reset("week", t0) == datetime(2026, 11, 23)
          and X.next_reset("month", t0) == datetime(2026, 12, 1))
    free = X.Budget({"limit": 1000, "maximum": 0.0}, {})
    free.add(10**9, t0)
    check("no limit: never stops; no maximum: raising never ends in 'all used'",
          X.Budget({"limit": 0}, {}).status(t0) == "ok" and free.status(t0) == "soft")
    check("when it resets, in words", X.fmt_reset(datetime(2026, 11, 20), t0) == "tomorrow at 00:00 (in 9 h 0 min)",
          X.fmt_reset(datetime(2026, 11, 20), t0))
    typed = {t: X.parse_amount(t) for t in ("3.5M", "750k", "2 000 000", "2'000'000", "2,000,000", "3,5M",
                                            "100 million", "1.234.567", "12.5k credits", "abc", "-5", "0", "")}
    check("a typed amount: 3.5M, 750k, separators (space, ', comma, dots), 3,5M, 'million'; nonsense is refused",
          typed == {"3.5M": 3.5e6, "750k": 750e3, "2 000 000": 2e6, "2'000'000": 2e6, "2,000,000": 2e6, "3,5M": 3.5e6,
                    "100 million": 1e8, "1.234.567": 1234567.0, "12.5k credits": 12500.0, "abc": None, "-5": None,
                    "0": None, "": None}, str(typed))
    check("token amounts in words: 100M, 3.5M, 1.23M (custom ones too)",
          [X.fmt_tokens(n) for n in (100_000_000, 3_500_000, 1_234_567, 999_999, 1700)] ==
          ["100M", "3.5M", "1.23M", "1M", "1.7k"], str([X.fmt_tokens(n) for n in (100_000_000, 3_500_000, 1_234_567,
                                                                                  999_999, 1700)]))
    # context windows ----------------------------------------------------------------------------
    mi = [X.max_input("anthropic", "claude-opus-5-5", {"context": 1_000_000, "max_tokens": 128_000}),
          X.max_input("anthropic", "claude-old", {}),
          X.max_input("openai", "gpt-6.2-sol-2026-06-01", {}), X.max_input("openai", "gpt-5.4-mini", {}),
          X.max_input("openai", "gpt-5.1-chat-latest", {}), X.max_input("openai", "o3-mini", {}),
          X.max_input("openai", "gpt-6.1-sol", {"context": 1_050_000}), X.max_input("openai", "gpt-9-nova", {}),
          X.max_input("openai", "gpt-9-nova", {"context": 272_000})]
    check("context windows: Claude = its reported window minus the reply (1M -> 968k; unknown: 200k); OpenAI = the "
          "documented maximum input (snapshots too), ChatGPT's own list caps it; unknown models 272k",
          mi == [968_000, 168_000, 922_000, 272_000, 111_616, 100_000, 922_000, 272_000, 204_000], str(mi))
    errs = ["prompt is too long: 250,000 tokens > 200,000 maximum",
            "input length and `max_tokens` exceed context limit: 990000 + 32000 > 1000000, decrease input length",
            "This model's maximum context length is 128000 tokens. However, your messages resulted in 130000 tokens.",
            "Your input exceeds the context window of this model. Please adjust your input and try again."]
    tl = [(lambda d: (d["limit"], d["actual"]))(json.loads(X.too_long(e))) for e in errs]
    check("'too long' answers are recognized, with what they say about the limit",
          all(X.is_too_long(e) for e in errs) and not X.is_too_long("slow down")
          and tl == [(200_000, 250_000), (968_000, 990_000), (128_000, 130_000), (None, None)], str(tl))

    # keys --------------------------------------------------------------------------------------
    cr = X.Credentials(secrets=SECRETS)
    cr.set("anthropic_key", "sk-ant-secret-123")
    raw = (Path(os.environ["APPDATA"]) / "PixelCat" / "keys.dat").read_bytes()
    check("keys.dat is encrypted (the key isn't in it as text) and reads back",
          b"sk-ant-secret" not in raw and X.Credentials(secrets=SECRETS).get("anthropic_key") == "sk-ant-secret-123")
    mem = X.MemoryCredentials({"openai_key": "sk-try"})
    mem.set("x", 1)
    check("a key that is only being checked is never written", X.Credentials(secrets=SECRETS).get("openai_key") is None
          and mem.get("openai_key") == "sk-try")

    # messages ------------------------------------------------------------------------------------
    system, msgs = AC.to_claude(HISTORY)
    roles = [m["role"] for m in msgs]
    u2 = msgs[2]["content"]
    ok_pairs = [b_["tool_use_id"] for b_ in u2 if b_["type"] == "tool_result"] == \
        [b_["id"] for b_ in msgs[1]["content"] if b_["type"] == "tool_use"]
    shot = [b_ for b_ in u2 if b_["type"] == "tool_result"][1]["content"]
    check("Claude: system apart, turns alternate, results first and paired with their calls by id",
          system == "You are the cat." and roles == ["user", "assistant", "user", "assistant", "user", "assistant", "user"]
          and ok_pairs and u2[0]["type"] == "tool_result" and u2[-1]["type"] == "text", str(roles))
    check("...screenshots as images (png/jpeg) - also inside a tool result",
          msgs[0]["content"][0]["source"]["media_type"] == "image/png"
          and any(x["type"] == "image" and x["source"]["media_type"] == "image/jpeg" for x in shot))
    check("...the model's own blocks replayed (thinking + signature), results answer their ids",
          msgs[3]["content"][0] == {"type": "thinking", "thinking": "hmm", "signature": "S"}
          and msgs[4]["content"][0]["tool_use_id"] == "toolu_x")
    check("...a call the user stopped gets a '(not run)' result before the new message",
          msgs[6]["content"][0]["type"] == "tool_result" and "not run" in msgs[6]["content"][0]["content"]
          and msgs[6]["content"][-1]["text"].startswith("stop that"))
    _s, stripped = AC.to_claude(HISTORY, strip_thinking=True)
    check("...and without thinking blocks when a request needs that", all(b_["type"] != "thinking"
                                                                        for m in stripped for b_ in m["content"]))
    instr, items = OC.to_responses(HISTORY)
    calls = [i for i in items if i.get("type") == "function_call"]
    outs = [i for i in items if i.get("type") == "function_call_output"]
    check("Responses: instructions apart; calls and outputs paired by call_id; the stopped call answered",
          instr == "You are the cat." and len(calls) == 4 and len(outs) == 4
          and [o["call_id"] for o in outs] == [c["call_id"] for c in calls] and "not run" in outs[-1]["output"])
    pic = [i for i in items if i.get("role") == "user" and isinstance(i.get("content"), list)
           and any(p.get("type") == "input_image" for p in i["content"])]
    check("...screenshots as input_image (the tool's after the outputs)", len(pic) == 2
          and items.index(pic[1]) > items.index(outs[1]))

    # Claude --------------------------------------------------------------------------------------
    creds = X.Credentials(secrets=SECRETS)
    info = {"adaptive": True, "effort": ["low", "medium", "high", "xhigh", "max"], "max_tokens": 128000}
    S["mode"] = "tool"
    tools = [{"type": "function", "function": {"name": "open_app", "description": "Open an app",
                                               "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}}]
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], tools, True, "high", creds, info))
    body, hdr = S["bodies"][-1], S["headers"][-1]
    check("Claude: streamed text, thinking shown, the tool call (with its id and arguments)",
          out["done"] is not None and out["done"][0] == "Hello there!" and out["thinking"] > 0
          and out["done"][1] == [{"id": "toolu_7", "name": "open_app", "arguments": {"name": "Notepad"}}],
          str(out["failed"] or out["done"]))
    check("...tokens counted (input + cache + output)", out["used"] == [("anthropic", 100 + 50 + 450)], str(out["used"]))
    check("...request: adaptive thinking (drop_block), effort, cached, eager tool input, fallbacks for Opus 5.5",
          body["thinking"].get("type") == "adaptive" and body["thinking"].get("block_binding") ==
          {"prefix_mismatch_behavior": "drop_block"} and body.get("output_config") == {"effort": "high"}
          and body.get("cache_control") == {"type": "ephemeral"} and body["tools"][0].get("eager_input_streaming")
          and body.get("fallbacks") == "default" and "thinking-binding-controls-2026-08-01" in hdr.get("anthropic-beta", "")
          and "server-side-fallback-2026-07-01" in hdr.get("anthropic-beta", "") and key_of(hdr) == "sk-ant-secret-123",
          json.dumps({k: body.get(k) for k in ("thinking", "output_config", "fallbacks")}))
    w = anthropic_worker("claude-opus-5-5", HISTORY[:2], tools, True, "high", creds, info)
    run_worker(w)
    check("...the reply's own blocks kept for next time (thinking with its signature)",
          w.raw is not None and w.raw[0].get("type") == "thinking" and w.raw[0].get("signature") == "SIG1")
    S["mode"] = "off_once"
    n0 = len(S["bodies"])
    out = run_worker(anthropic_worker("claude-sonnet-5-5", HISTORY[:2], None, False, "max", creds, info))
    sent = [bd.get("thinking", {}).get("type") for bd in S["bodies"][n0:]]
    check("thinking off: 'disabled' refused -> 'between_tools' (effort capped at high) - and remembered",
          out["done"] is not None and sent == ["disabled", "between_tools"]
          and S["bodies"][-1]["output_config"]["effort"] == "high", str(sent))
    n0 = len(S["bodies"])
    run_worker(anthropic_worker("claude-sonnet-5-5", HISTORY[:2], None, False, "low", creds, info))
    check("...the next request starts with what worked", [bd.get("thinking", {}).get("type")
                                                          for bd in S["bodies"][n0:]] == ["between_tools"])
    S["mode"] = "rate"
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info))
    det = json.loads(out["failed"][1]) if out["failed"] and out["failed"][0] == "limit" else {}
    left = (datetime.fromtimestamp(det["reset"]) - datetime.now()).total_seconds() if det.get("reset") else 0
    check("rate limit: reported as the provider's limit, with when it lifts", det.get("why") == "rate"
          and 500 < left < 700, str(out["failed"]))
    S["mode"] = "credits"
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info))
    check("credits used up: the provider's limit (no reset)", out["failed"] and out["failed"][0] == "limit"
          and json.loads(out["failed"][1])["why"] == "credits", str(out["failed"]))
    S["mode"] = "refusal"
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info))
    check("a refusal is reported (not shown as an empty answer)", out["failed"] and out["failed"][0] == "refused",
          str(out["failed"]))
    S["mode"] = "text"
    from pixelcat.adapters.llm import ant_tool
    d = ant_tool.signin_dir()                         # (what ant writes when signing in)
    (d / "configs").mkdir(parents=True, exist_ok=True)
    (d / "credentials").mkdir(parents=True, exist_ok=True)
    (d / "configs" / "default.json").write_text(json.dumps({"authentication": {"type": "user_oauth"}}))
    (d / "credentials" / "default.json").write_text(json.dumps({"access_token": "tok-signin",
                                                                "expires_at": int(time.time()) + 3600}))
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-env"     # (must not be used for the sign-in)
    run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info, auth="signin"))
    os.environ.pop("ANTHROPIC_API_KEY")
    shutil.rmtree(d)
    check("signed in with Anthropic: Pixel Cat's own sign-in is used - not the saved key, not the environment",
          key_of(S["headers"][-1]) == "tok-signin", key_of(S["headers"][-1])[:12])
    ms = AC.list_models(creds)
    check("model list with capabilities and the context window", [m["id"] for m in ms] == ["claude-opus-5-5", "claude-haiku-5-5"]
          and ms[0]["adaptive"] and ms[0]["effort"][-1] == "max" and ms[1]["effort"] == []
          and ms[0]["context"] == 1_000_000)
    S["mode"] = "too_long"
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info))
    S["mode"] = "text"
    check("Claude: a request that is too long is reported as such (with the numbers)",
          out["failed"] is not None and out["failed"][0] == "too_long"
          and json.loads(out["failed"][1])["limit"] == 200_000, str(out["failed"]))
    # Claude's web search -------------------------------------------------------------------------
    S["mode"] = "search"
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], tools, True, "medium", creds, info), web=True)
    body = S["bodies"][-1]
    ws = [t for t in body.get("tools", []) if t.get("name") == "web_search"]
    text = out["done"][0] if out["done"] else ""
    check("Claude web search (on): Anthropic's web_search tool next to the cat's tools; 'searching' shown; the "
          "answer comes with its sources as links",
          ws == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}] and len(body["tools"]) == 2
          and "Cats are napping." in text and "**Sources:** [Cat News](https://news.example.org/cats)" in text
          and X.SEARCHING in out["status"], f"{ws} {text!r} {out['status']}")
    run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], tools, True, "medium", creds, info), web=False)
    run_worker(anthropic_worker("claude-old", HISTORY[:2], tools, True, "medium", creds,
                                dict(info, web_search=False)), web=True)
    check("...off (Settings) or a model without it: no web search tool",
          all(t.get("name") != "web_search" for b_ in S["bodies"][-2:] for t in b_.get("tools", [])))
    S["mode"] = "pause_once"
    n0 = len(S["bodies"])
    out = run_worker(anthropic_worker("claude-opus-5-5", HISTORY[:2], None, True, "medium", creds, info), web=True)
    cont = S["bodies"][-1]["messages"][-1] if len(S["bodies"]) > n0 else {}
    check("a reply paused during long searches (pause_turn) is sent back and continued; tokens of both counted",
          len(S["bodies"]) == n0 + 2 and cont.get("role") == "assistant"
          and cont["content"][0].get("type") == "server_tool_use" and out["done"] is not None
          and "Cats are napping." in out["done"][0] and len(out["used"]) == 2, str(cont)[:200])
    S["mode"] = "search_off"
    n0 = len(S["bodies"])
    w_ = anthropic_worker("claude-sonnet-5-5", HISTORY[:2], None, True, "medium", creds, info)
    out = run_worker(w_, web=True)
    check("web search turned off for the organization: asked again without it, and the chat is told once",
          out["done"] is not None and len(S["bodies"]) == n0 + 2 and "turned off" in w_.note, w_.note)
    S["mode"] = "text"

    # OpenAI --------------------------------------------------------------------------------------
    creds.set("openai_key", "sk-oa-1")
    S["oa_mode"] = "tool"
    out = run_worker(openai_worker("gpt-6.1", HISTORY[:2], tools, True, "high", creds, "key"))
    body = S["bodies"][-1]
    check("OpenAI: streamed text, the tool call, tokens counted",
          out["done"] == ("Hi from GPT", [{"id": "call_9", "name": "open_app", "arguments": {"name": "Paint"}}])
          and out["used"] == [("openai", 340)], str(out["done"] or out["failed"]))
    check("...request: Responses API, store false, stream, reasoning effort, tools not strict",
          body["store"] is False and body["stream"] is True and body["reasoning"] == {"effort": "high"}
          and body["tools"][0]["strict"] is False and body["instructions"] == "You are the cat."
          and S["headers"][-1]["Authorization"] == "Bearer sk-oa-1")
    S["oa_mode"] = "no_effort"
    n0 = len(S["bodies"])
    out = run_worker(openai_worker("o5-mini", HISTORY[:2], None, True, "medium", creds, "key"))
    check("reasoning.effort refused -> asked again without it", out["done"] is not None
          and ["reasoning" in bd for bd in S["bodies"][n0:]] == [True, False])
    S["oa_mode"] = "plan"
    out = run_worker(openai_worker("gpt-6.1", HISTORY[:2], None, True, "medium", creds, "key"))
    check("the ChatGPT plan's limit: the provider's limit (no reset time made up)",
          out["failed"] and json.loads(out["failed"][1]) == {"provider": "openai", "why": "plan", "detail": "limit",
                                                             "reset": None}, str(out["failed"]))
    S["oa_mode"] = "too_long"
    out = run_worker(openai_worker("gpt-6.1", HISTORY[:2], None, True, "medium", creds, "key"))
    check("OpenAI: context_length_exceeded is reported as 'too long'", out["failed"] is not None
          and out["failed"][0] == "too_long", str(out["failed"]))
    S["oa_mode"] = "search"
    out = run_worker(openai_worker("gpt-6.1", HISTORY[:2], tools, True, "medium", creds, "key"), web=True)
    body = S["bodies"][-1]
    text = out["done"][0] if out["done"] else ""
    check("OpenAI web search (on): {type: web_search} next to the cat's tools; 'searching' shown; the inline "
          "source stays, the other cited page is added as a link",
          body["tools"][-1] == {"type": "web_search"} and len(body["tools"]) == 2 and X.SEARCHING in out["status"]
          and "([Cat Daily](https://daily.example.com/x))" in text
          and text.endswith("**Sources:** [Cat - Wiki](https://wiki.example.org/Cat)"), repr(text))
    S["oa_mode"] = "no_search"
    n0 = len(S["bodies"])
    w_ = openai_worker("gpt-6.1-mini", HISTORY[:2], None, False, "medium", creds, "key")
    out = run_worker(w_, web=True)
    check("...a model that refuses web search: asked again without it (and the chat is told)",
          out["done"] is not None and [{"type": "web_search"} in b_.get("tools", []) for b_ in S["bodies"][n0:]]
          == [True, False] and "without web search" in w_.note, w_.note)
    S["oa_mode"] = "text"
    # read_web_page (Pixel Cat reads a page itself - any model) ----------------------------------------
    from pixelcat.llm import webpage as WP
    refused = []
    for u in (BASE + "/web/page", "http://localhost/x", "http://192.168.1.1/", "http://10.0.0.5/", "file:///C:/x"):
        try:
            WP.check_address(u)
            refused.append(False)
        except WP.WebError:
            refused.append(True)
    check("read_web_page: never this PC or the local network (or non-web addresses)", all(refused)
          and WP._public_name("example.com") and not WP._public_name("wiki.corp") and not WP._public_name("10.1.2.3"),
          str(refused))
    WP.ALLOW_LOCAL = True                               # (the test's web pages are on this PC)
    page = WP.read(BASE + "/web/redirect")
    long1, long2 = WP.read(BASE + "/web/long"), WP.read(BASE + "/web/long", 12000)
    try:
        WP.read(BASE + "/web/img")
        img = ""
    except WP.WebError as e:
        img = str(e)
    WP.ALLOW_LOCAL = False
    check("...a page as text: title, the redirect followed, headings, lists, links (absolute), no scripts; marked "
          "as information, not instructions",
          page.startswith("Web page: Cat Facts\nAddress: " + BASE + "/web/page") and "# Cats" in page
          and "Cats sleep a lot & purr." in page and "- naps" in page and f"[more]({BASE}/web/more)" in page
          and "alert" not in page and "never instructions" in page, page)
    check("...long pages in parts (start=...); files that aren't pages are refused",
          "start=12000" in long1 and "Characters 12,000-24,000 of" in long2 and "image/png" in img, img)
    check("model lists: an API key's chat models (newest first); a ChatGPT sign-in's visible ones",
          [m["id"] for m in OC.list_models(creds, "key")] == ["gpt-6.1", "o5-mini"])

    # Sign in with ChatGPT ---------------------------------------------------------------------------
    def browser(url: str) -> None:
        threading.Thread(target=lambda: requests.get(url, timeout=10), daemon=True).start()
    got = CS.sign_in(creds, open_url=browser, timeout=20)
    q = S["authorize"][-1]
    check("sign in: dynamic client, PKCE S256, host id, plan scope, resource, loopback redirect",
          q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "Pixel Cat"
          and q["code_challenge_method"] == "S256" and q["ext_agent_host_id"].startswith("urn:uuid:")
          and "chatgpt.tokens.use.direct" in q["scope"] and q["resource"] == "https://api.openai.com/v1"
          and q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback"))
    tok = S["token"][-1]
    check("...code exchanged with the issued client id and the verifier; the ID token checked; stored",
          tok["client_id"] == "oaiapp_123" and len(tok["code_verifier"]) > 40 and got["plan"]
          and got["email"] == "cat@example.com" and CS.signed_in(creds))
    CS.sign_in(creds, open_url=browser, timeout=20)
    q2 = S["authorize"][-1]
    check("...the next sign-in reuses the issued client id (no name hint, with the ID token hint, same host id)",
          q2["client_id"] == "oaiapp_123" and "agent_name_hint" not in q2 and q2.get("id_token_hint")
          and q2["ext_agent_host_id"] == q["ext_agent_host_id"])
    S["bad_sig"] = True
    try:
        CS.sign_in(creds, open_url=browser, timeout=20)
        bad = "accepted!"
    except CS.SignInError as e:
        bad = str(e)
    S["bad_sig"] = False
    check("...an ID token with a bad signature is refused", "signature" in bad, bad)
    S["oa_mode"] = "tool"
    out = run_worker(openai_worker("gpt-6.1-sol", HISTORY[:2], None, True, "medium", creds, "chatgpt"))
    check("requests with the ChatGPT sign-in: the access token", S["headers"][-1]["Authorization"] == "Bearer chatgpt-AT"
          and out["done"] is not None)
    c = creds.get("chatgpt")
    c.update(access_token="chatgpt-old", expires_at=time.time() - 10)
    creds.set("chatgpt", c)
    out = run_worker(openai_worker("gpt-6.1-sol", HISTORY[:2], None, True, "medium", creds, "chatgpt"))
    check("...an expired sign-in is renewed first (refresh token rotated)", out["done"] is not None
          and S["headers"][-1]["Authorization"] == "Bearer chatgpt-new" and creds.get("chatgpt")["refresh_token"] == "R2"
          and S["token"][-1]["grant_type"] == "refresh_token" and S["token"][-1]["client_id"] == "oaiapp_123")
    cg = OC.list_models(creds, "chatgpt")
    check("...its model list: only visible models, with the most context the plan's model takes",
          [m["id"] for m in cg] == ["gpt-6.1-sol"] and cg[0].get("context") == 1_050_000, str(cg))
    ok_out = CS.sign_out(creds)
    check("sign out: revoked at OpenAI, tokens gone, the issued client id kept",
          ok_out and S["revoked"][-1]["token"] == "R2" and not CS.signed_in(creds)
          and creds.get("chatgpt") == {"client_id": "oaiapp_123", "validated": True})
    creds.set("chatgpt", None)                          # a first sign-in again...
    S["refuse_codes"] = 1                               # ...where OpenAI doesn't take the first code
    n0, asked = len(S["authorize"]), []
    got = CS.sign_in(creds, open_url=browser, timeout=20, again=lambda: asked.append(1))
    qs = S["authorize"][n0:]
    check("a code OpenAI refuses (invalid_grant): asked once more, with the issued client id - then signed in",
          [q["client_id"] for q in qs] == ["dynamic_agent_client", "oaiapp_123"] and "agent_name_hint" not in qs[1]
          and asked == [1] and got["plan"] and CS.signed_in(creds)
          and [t["client_id"] for t in S["token"][-2:]] == ["oaiapp_123", "oaiapp_123"], str([q["client_id"] for q in qs]))
    creds.set("chatgpt", None)
    S["refuse_codes"] = 2
    try:
        CS.sign_in(creds, open_url=browser, timeout=20)
        msg = "signed in!"
    except CS.SignInError as e:
        msg = str(e)
    S["refuse_codes"] = 0
    check("...refused twice: OpenAI's own reason is shown; that registration isn't kept (it never worked)",
          "Invalid authorization code" in msg and not creds.get("chatgpt"), msg)
    creds.set("chatgpt", {"client_id": "oaiapp_stale"})   # a registration that never finished (wrong workspace)
    n0 = len(S["authorize"])
    CS.sign_in(creds, open_url=browser, timeout=20)
    check("a registration that never worked is ignored: a fresh one, with OpenAI's account/workspace picker",
          S["authorize"][n0]["client_id"] == "dynamic_agent_client" and creds.get("chatgpt")["validated"]
          and creds.get("chatgpt")["client_id"] == "oaiapp_123", S["authorize"][n0]["client_id"])

    # in the app ------------------------------------------------------------------------------------
    app_checks(creds)
    SRV.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


def wait(cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def app_checks(creds) -> None:  # noqa: C901 - a linear test
    from pixelcat import persistence
    from pixelcat.app import PixelCatApp
    from pixelcat.bootstrap import build_platform, build_providers
    from pixelcat.adapters.windows import win32
    from pixelcat.logsetup import setup_logging
    from pixelcat.settings import load_config

    setup_logging(False)
    win32.set_dpi_awareness()
    cfg = load_config()
    saved = persistence.SavedState(autostart_asked=True)
    pet = PixelCatApp(app, cfg, saved, selftest=1, platform=build_platform(), providers=build_providers())
    pet.save = lambda: None
    pet.creds = creds
    pet.creds.set("anthropic_key", "sk-ant-secret-123")
    # the key dialog: a bad key, then a good one ----------------------------------------------------
    pet.creds.set("anthropic_key", None)
    from pixelcat.ui import dialogs
    dialogs.KeyDialog.show_near = lambda self, *a: None   # (never shown: no focus taken from the user)
    pet._ask_key("anthropic")
    dlg = pet._key_dialog
    dlg.field.setText("sk-ant-bad")
    dlg._save()
    wait(lambda: dlg.save.isEnabled())
    refused = dlg.status.text()
    dlg.field.setText("sk-ant-good")
    dlg._save()
    wait(lambda: pet.creds.get("anthropic_key") == "sk-ant-good")
    check("key dialog: a wrong key is refused in the dialog; a good one is checked, kept, the models loaded",
          "wasn't accepted" in refused and pet.creds.get("anthropic_key") == "sk-ant-good"
          and pet._ext_auth("anthropic") == "key" and len(pet._ext_models("anthropic")) == 2, refused)
    for p in list(pet._popups):
        try:
            p._finish(False)                         # (answered: the app forgets it too)
        except RuntimeError:
            pet._popups.remove(p) if p in pet._popups else None
    # the menu ---------------------------------------------------------------------------------------
    items = {i.key: i for i in pet._menu_items()}
    ai = next(c for c in items["settings"].children if c.key == "set_model")
    ext = next(c for c in ai.children if c.key == "ext")
    claude = next(c for c in ext.children if c.key == "ext_anthropic")
    keys = [c.key for c in claude.children]
    check("Settings > AI model > External AI models: Claude (connected: Model, limit, resets, raise up to, used), "
          "ChatGPT and Copilot; Thinking; Effort; Web search",
          [c.key for c in ext.children] == ["ext_anthropic", "ext_openai", "ext_copilot", "ext:thinking", "ext_effort",
                                            "ext:websearch"]
          and {"ext_models_anthropic", "ext_limit_anthropic", "ext_period_anthropic", "ext_max_anthropic",
               "ext:used:anthropic", "ext:out:anthropic"} <= set(keys), str(keys))
    on = next(c for c in ext.children if c.key == "ext:websearch").checked
    pet._on_menu("ext:websearch")
    check("Web search is on by default and can be switched off (and on)", on is True
          and saved.ext_settings["web_search"] is False)
    pet._on_menu("ext:websearch")
    from pixelcat.ui.chat import md_to_html
    linked = md_to_html("See [Cat News](https://news.example.org/a?b=1&c=2) or [x](javascript:alert(1)).")
    spec, a = pet.toolbox.prepare("read_web_page", {"url": "example.com/cats", "start": "12"})
    check("links in the chat are clickable (web addresses only); read_web_page asks first (ALWAYS: group 'web')",
          '<a href="https://news.example.org/a?b=1&amp;c=2"' in linked and "javascript" not in linked.split("<a")[-1][:40]
          and linked.count("<a ") == 1 and a["url"] == "https://example.com/cats" and a["start"] == 12
          and spec.confirm == "soft" and spec.group == "web" and spec.background, linked)
    m = pet.menu
    m.items = pet._menu_items()
    m.expanded = {"settings", "set_model", "ext", "ext_anthropic"}
    m.u = 3
    w_, h_ = m._layout()
    m.resize(w_, h_)
    m.grab().save(str(ROOT / "build" / "preview" / "menu_external.png"))
    pet._on_menu("ext:limit:anthropic:50000")
    pet._on_menu("ext:effort:low")
    pet._on_menu("ext:thinking")
    check("menu: limit, effort and thinking change", pet._budget("anthropic").limit == 50000
          and saved.ext_settings["effort"] == "low" and saved.ext_settings["thinking"] is False)
    # a custom token limit (Token limit -> Custom...) ---------------------------------------------------
    lim_kids = next(c for c in pet._provider_menu("anthropic").children if c.key == "ext_limit_anthropic").children
    pet._on_menu("ext:custom:anthropic")
    dlg = pet._key_dialog
    answers = []
    for text in ("lots", "500", "150M", "3.5M"):
        dlg.field.setText(text)
        dlg._save()
        answers.append(dlg.status.text() if dlg.save.isEnabled() else "ok")
    lim_kids2 = next(c for c in pet._provider_menu("anthropic").children if c.key == "ext_limit_anthropic").children
    custom = next(c for c in lim_kids2 if c.key == "ext:custom:anthropic")
    check("Token limit: presets up to 100M, then Custom... - a typed limit: nonsense, too little and over 100M are "
          "refused in the dialog; 3.5M is kept and shown as 'Custom: 3.5M tokens'",
          [c.label for c in lim_kids][-2:] == ["100M tokens", "Custom..."] and "understand" in answers[0]
          and "at least 1k" in answers[1] and "100M" in answers[2] and answers[3] == "ok"
          and pet._budget("anthropic").limit == 3_500_000 and pet._budget("anthropic").allowed() == 3_500_000
          and custom.checked and custom.label == "Custom: 3.5M tokens", str(answers))
    pet._on_menu("ext:thinking")
    pet._on_menu("model:anthropic:claude-opus-5-5")
    check("picking a Claude model makes it the default (shown as 'Claude Opus 5.5 (Anthropic)')",
          pet.cfg.model == "anthropic:claude-opus-5-5" and X.label(pet.cfg.model) == "Claude Opus 5.5 (Anthropic)")
    # chatting until the limit -------------------------------------------------------------------------
    S["mode"] = "text"
    pet._ext_p("anthropic").update(limit=1000, maximum=2.0, period="day")
    saved.ext_usage["anthropic"] = {}
    pet._show_chat_quietly()
    chat = pet.chat

    def say(text: str) -> None:
        n = len(pet._popups)
        pet._on_user_message(text)
        wait(lambda: (pet.worker is None and not pet._task_active) or chat.choice.isVisible() or len(pet._popups) > n)

    say("hi")
    used1 = pet._budget("anthropic").used()
    say("and again")
    used2, card0 = pet._budget("anthropic").used(), chat.choice.isVisible()
    say("a third time")
    card1 = chat.choice.isVisible()
    labels = [b_.text() for b_ in chat._choice_btns]
    check("chatting counts tokens; past the limit the chat asks: SWITCH MODEL / RAISE TO 1.7K / STOP",
          (used1, used2, card0) == (600, 1200, False) and card1 and labels == ["SWITCH MODEL", "RAISE TO 1.7K", "STOP"],
          f"{used1} {used2} {labels}")
    chat._choice_btns[1].click()
    wait(lambda: not pet._task_active)
    check("RAISE: the request goes on with the higher limit", pet._budget("anthropic").allowed() == 1700
          and pet._budget("anthropic").used() == 1800, str((pet._budget("anthropic").allowed(),
                                                            pet._budget("anthropic").used())))
    say("more")
    labels = [b_.text() for b_ in chat._choice_btns] if chat.choice.isVisible() else []
    chat._choice_btns[1].click() if labels else None
    wait(lambda: not pet._task_active)
    check("...asked again - raising goes up to the maximum (2k)", labels[1:2] == ["RAISE TO 2K"]
          and pet._budget("anthropic").used() == 2400, str(labels))
    n_pop = len(pet._popups)
    say("even more")
    wait(lambda: len(pet._popups) > n_pop)
    pop = pet._popups[-1] if len(pet._popups) > n_pop else None
    text = pop.text.text() if pop is not None else ""
    check("all tokens used: a popup says when the limit resets", pop is not None and pop.title == "All tokens used"
          and "resets" in text and "tomorrow at 00:00" in text, text[:160])
    if pop is not None:
        pop._finish(False)
    wait(lambda: not pet._task_active)
    # switching model from the card ---------------------------------------------------------------------
    saved.ext_usage["anthropic"] = {"start": X.period_start("day", datetime.now()).timestamp(), "used": 1000,
                                    "allowed": 1000}
    pet.chat_model = None
    pet._on_user_message("switch please")
    wait(lambda: chat.choice.isVisible())
    chat._choice_btns[0].click()
    shown = pet.model_menu.isVisible()
    keys = [it.key for it in pet.model_menu.items]
    n0 = len(S["bodies"])
    pet._ext_p("anthropic")["limit"] = 0                # (the other model: no limit for the test)
    pet._on_model_menu("chatmodel:anthropic:claude-haiku-5-5")
    wait(lambda: not pet._task_active)
    check("SWITCH MODEL: the model menu (with the Claude models) - picking one goes on with it",
          shown and "chatmodels_anthropic" in keys and len(S["bodies"]) == n0 + 1
          and S["bodies"][-1]["model"] == "claude-haiku-5-5", str(keys))
    pet.model_menu.close()
    # a provider's rate limit ------------------------------------------------------------------------------
    S["mode"] = "rate"
    n_pop = len(pet._popups)
    pet._on_user_message("hello?")
    wait(lambda: len(pet._popups) > n_pop)
    pop = pet._popups[-1] if len(pet._popups) > n_pop else None
    text = pop.text.text() if pop is not None else ""
    check("the provider's rate limit: a popup with when it lifts", pop is not None and pop.title == "Rate limit reached"
          and "lifts" in text and "today at" in text, text[:160])
    if pop is not None:
        pop._finish(False)
    wait(lambda: not pet._task_active)
    # context windows in the app -----------------------------------------------------------------------------
    local, default = pet.chat_model, pet.cfg.model
    pet.chat_model, pet.cfg.model = None, "qwen3.5:9b"
    b_local = pet._ctx_budget()
    pet.chat_model, pet.cfg.model = "anthropic:claude-haiku-5-5", default
    b_claude = pet._ctx_budget()
    pet._ext_p("anthropic")["limit"] = 0
    S["mode"] = "too_long_once"
    n0 = len(S["bodies"])
    say("summarize everything we did")
    reply = next((mm.text for mm in reversed(chat.msgs) if mm.role == "cat"), "")
    learned = (pet._ext_p("anthropic").get("ctx_learned") or {}).get("claude-haiku-5-5", 0)
    noted = any("shortened the older parts" in mm.text for mm in chat.msgs)
    check("Claude uses its own window (968k), local models keep num_ctx; 'too long' -> a smaller budget is kept for "
          "that model and the request is sent again, shortened",
          b_local == pet.cfg.num_ctx and b_claude == 968_000 and len(S["bodies"]) == n0 + 2 and "Hello" in reply
          and X.MIN_BUDGET <= learned < 968_000 and pet._ctx_budget() == learned and noted,
          f"{b_local} {b_claude} bodies+{len(S['bodies']) - n0} learned={learned} reply={reply[:40]!r}")
    S["mode"] = "too_long"
    n0 = len(S["bodies"])
    say("and again")
    err = next((mm.text for mm in reversed(chat.msgs) if mm.role == "error"), "")
    check("...still too long after two shorter tries: the chat says so (new chat)",
          len(S["bodies"]) == n0 + 3 and "longer than" in err and not pet._task_active, err[:120])
    S["mode"] = "text"
    pet._ext_p("anthropic").pop("ctx_learned", None)
    pet.chat_model = local
    # Sign in with ChatGPT from the menu (the browser is faked) ------------------------------------------------
    import webbrowser
    webbrowser.open = lambda url, *a, **k: threading.Thread(target=lambda: requests.get(url, timeout=10),
                                                            daemon=True).start()
    pet.creds.set("chatgpt", None)
    pet._ext_p("openai").pop("auth", None)
    S["refuse_codes"] = 1
    n_pop = len(pet._popups)
    pet._browser_start("openai")
    wait_pop = pet._popups[-1] if len(pet._popups) > n_pop else None
    seen: set[str] = set()

    def signed() -> bool:
        try:
            seen.add(wait_pop.text.text())
        except (RuntimeError, AttributeError):
            pass
        return pet._ext_auth("openai") == "chatgpt" and bool(pet._ext_models("openai"))
    wait(signed, 30)
    check("menu sign-in with ChatGPT: 'Finish in your browser' while it runs (and why it asks twice), then signed in",
          wait_pop is not None and wait_pop.title == "Finish in your browser" and any("One more step" in t for t in seen)
          and pet._ext_auth("openai") == "chatgpt" and wait_pop not in pet._popups, f"{len(seen)} texts")
    S["refuse_codes"] = 0
    for p in list(pet._popups):
        try:
            p._finish(False)
        except RuntimeError:
            pass
    # START OVER while OpenAI shows an error page (which never comes back to the cat)
    pet.creds.set("chatgpt", None)
    pet._ext_p("openai").pop("auth", None)
    S["error_page"] = True
    n_auth, n_pop = len(S["authorize"]), len(pet._popups)
    pet._browser_start("openai")
    wait(lambda: len(S["authorize"]) > n_auth, 10)
    first = pet._popups[-1] if len(pet._popups) > n_pop else None
    S["error_page"] = False
    if first is not None:
        first._finish(True)                             # START OVER
    wait(lambda: pet._ext_auth("openai") == "chatgpt", 30)
    qs = S["authorize"][n_auth:]
    left = [p.title for p in pet._popups if p.title in ("Finish in your browser", "Sign in with ChatGPT")]
    check("START OVER (OpenAI showed an error page): a fresh sign-in starts and finishes; no stray popups",
          first is not None and first.title == "Finish in your browser" and len(qs) == 2
          and all(q["client_id"] == "dynamic_agent_client" for q in qs) and pet._ext_auth("openai") == "chatgpt"
          and not left, f"{len(qs)} authorizations, left {left}")
    for p in list(pet._popups):
        try:
            p._finish(False)
        except RuntimeError:
            pass
    # ChatGPT plan indicator --------------------------------------------------------------------------------
    pet.creds.set("chatgpt", {"client_id": "oaiapp_123", "access_token": "chatgpt-AT", "refresh_token": "R1",
                              "expires_at": time.time() + 3000})
    pet._ext_p("openai")["auth"] = "chatgpt"
    pet._ext_p("openai")["models"] = [{"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol"}]
    pet._set_chat_model("openai:gpt-6.1-sol")
    wait(lambda: "Using ChatGPT plan" in chat.model_bar.text(), 10)
    check("a chat on the ChatGPT plan says so, with Manage usage; the model button shows the model",
          chat.model_bar.isVisible() and "Using ChatGPT plan" in chat.model_bar.text()
          and "chatgpt.com/settings/usage" in chat.model_bar.text() and chat.model_btn.text() == "GPT-6.1",
          chat.model_btn.text())
    for p in list(pet._popups):
        try:
            p._finish(False)                         # (answered: the app forgets it too)
        except RuntimeError:
            pet._popups.remove(p) if p in pet._popups else None
    chat.hide()


if __name__ == "__main__":
    sys.exit(main())
