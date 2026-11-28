"""GitHub Copilot test (developer tool).  Nothing goes to GitHub's Copilot service and no credits
are used: GitHub's real Copilot runtime runs against a local fake model server (the SDK's
"bring your own key" mode).  The runtime itself is downloaded once from GitHub's official release
page into build/copilottest-local (about 100 MB, kept between runs).  APPDATA is moved to
build/copilottest, so the real keys and save are never touched.

* Credit limit: limit -> raise for now -> resets at the start of a month; credits as GitHub shows them.
* Effort mapping, the quota that matters, how unseen history is told to the runtime.
* Sign in with GitHub (device flow) against a fake github.com.
* The runtime: set up with progress; it gets the cat's own system prompt and only the cat's tools;
  text, tool calls (several, a screenshot as a result), the results going back into the same turn;
  Stop while a tool waits, then a new message; errors (sign-in, allowance, rate limit, server).
* In the app: the menu (Copilot, credit limit, the plan's allowance); a tool that needs a yes is
  asked in the chat (declined here - nothing opens); credit limit reached -> SWITCH / RAISE / STOP;
  the allowance used up -> popup; new chat and quit end the session and the runtime.
Usage:  python tools/copilot_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "build" / "copilottest"
shutil.rmtree(TEST, ignore_errors=True)
(TEST / "AppData").mkdir(parents=True)
os.environ["APPDATA"] = str(TEST / "AppData")
os.environ["LOCALAPPDATA"] = str(ROOT / "build" / "copilottest-local")      # (the runtime stays between runs)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("PIXELCAT_INSTANCE", "copilottest")
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelcat.bootstrap import build_platform  # noqa: E402
from pixelcat.adapters.llm import copilot_client as CC  # noqa: E402
from pixelcat.llm import external as X  # noqa: E402
from pixelcat.adapters.llm import github_signin as GS  # noqa: E402
from pixelcat.llm.qt_chat import QtChatWorker  # noqa: E402
from pixelcat.ports.llm import ChatRequest  # noqa: E402

SECRETS = build_platform().secrets                 # keys.dat, as the app keeps it

app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


# ------------------------------------------------------------------ a fake model server and github.com
S: dict = {"script": [], "bodies": [], "device_polls": 0}


def reply(text: str = "", calls: list | None = None, status: int = 200, usage: tuple = (100, 10)) -> dict:
    return {"text": text, "calls": calls or [], "status": status, "usage": usage}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # noqa: D401
        pass

    def _json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/page"):
            data = b"<html><head><title>Cat page</title></head><body><h1>Cats</h1><p>Cats nap all day.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/gh/user"):
            ok = self.headers.get("Authorization") == "Bearer gho_fake"
            self._json(200 if ok else 401, {"login": "octocat"} if ok else {"message": "Bad credentials"})
            return
        self._json(200, {"data": [{"id": "fake-model", "object": "model"}]})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) or b"{}"
        if self.path.startswith("/gh/"):
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if self.path.startswith("/gh/device"):
                self._json(200, {"device_code": "DEV", "user_code": "ABCD-1234", "interval": 1, "expires_in": 60,
                                 "verification_uri": "https://github.com/login/device"} if form.get("client_id") == "Iv-pixel"
                           else {"error": "bad_client"})
            else:
                S["device_polls"] += 1
                if S["device_polls"] < 2:
                    self._json(200, {"error": "authorization_pending"})
                else:
                    self._json(200, {"access_token": "gho_fake", "token_type": "bearer"} if form.get("device_code") == "DEV"
                               else {"error": "expired_token"})
            return
        body = json.loads(raw)
        S["bodies"].append(body)
        spec = S["script"].pop(0) if S["script"] else reply("(nothing scripted)")
        if spec["status"] != 200:
            st = spec["status"]
            self._json(st, {"error": {"message": spec["text"] or f"fake {st}", "type": "x",
                                      "code": "insufficient_quota" if st == 402 else "x"}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        base = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": body.get("model", "")}

        def ev(obj: dict) -> None:
            self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
            self.wfile.flush()
        for part in ([spec["text"][:6], spec["text"][6:]] if spec["text"] else []):
            if part:
                ev({**base, "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]})
        for i, (cid, name, args) in enumerate(spec["calls"]):
            ev({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": cid, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": None}]})
        ev({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if spec["calls"] else "stop"}],
            "usage": {"prompt_tokens": spec["usage"][0], "completion_tokens": spec["usage"][1],
                      "total_tokens": sum(spec["usage"])}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


SRV = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
MODELS = [{"id": "fake-model", "name": "Fake Model", "efforts": [], "vision": True},
          {"id": "think-model", "name": "Think Model", "efforts": ["low", "medium", "high"], "vision": True}]
CC.BYOK = {"provider": {"name": "fake", "type": "openai", "wire_api": "completions", "base_url": BASE + "/v1",
                        "api_key": "not-a-real-key"},
           "models": MODELS, "login": "octocat",
           "quota": {"kind": "ai_credits", "used": 3120.5, "total": 20000.0, "unlimited": False,
                     "reset": "2026-12-01T00:00:00Z", "overage_ok": False}}
GS.URLS.update(device=BASE + "/gh/device", token=BASE + "/gh/token", user=BASE + "/gh/user")

TOOLS = [{"type": "function", "function": {"name": "get_time", "description": "Get the time.",
                                            "parameters": {"type": "object", "properties": {}}}},
         {"type": "function", "function": {"name": "look_at_screen", "description": "Screenshot.",
                                            "parameters": {"type": "object", "properties": {}}}}]
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def run_worker(chat, history: list, model: str = "fake-model", think: bool = True, effort: str = "medium",
               timeout: float = 60.0, info: dict | None = None, cancel_after: float | None = None,
               web: bool = False, on_perm=None) -> dict:
    out: dict = {"tokens": [], "thinking": 0, "done": None, "failed": None, "used": [], "credits": []}
    req = ChatRequest(model, history, TOOLS, think, effort, X.MemoryCredentials({}), "key",
                      info if info is not None else next((m for m in MODELS if m["id"] == model), {}), web=web,
                      history=history, system="You are Pixel, a test cat.", conversation=chat)
    w = QtChatWorker(lambda events: CC.CopilotJob(req, events), provider="copilot", model=model, local=False)
    if on_perm is not None:
        w.permission.connect(on_perm)
    w.token.connect(lambda t: out["tokens"].append(t))
    w.done.connect(lambda c, calls: out.__setitem__("done", (c, calls)))
    w.failed.connect(lambda k, d: out.__setitem__("failed", (k, d)))
    w.used.connect(lambda p, n: out["used"].append((p, n)))
    w.credits.connect(lambda c: out["credits"].append(c))
    w.start()
    t0 = time.time()
    end = t0 + timeout
    while (out["done"] is None and out["failed"] is None) and time.time() < end:
        app.processEvents()
        if cancel_after is not None and time.time() - t0 > cancel_after:
            w.cancel()
            break
        time.sleep(0.01)
    w.wait(15000)
    app.processEvents()
    return out


def assistant(text: str, calls: list) -> dict:
    m: dict = {"role": "assistant", "content": text}
    if calls:
        m["tool_calls"] = [{"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
    return m


def user_texts(body: dict) -> list[str]:
    out = []
    for m in body.get("messages", []):
        if m.get("role") == "user":
            c = m.get("content")
            out.append(c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if isinstance(p, dict)))
    return out


def main() -> int:  # noqa: C901 - a linear test script
    # credit limit ------------------------------------------------------------------------------------
    s, u = {"credit_limit": 1000}, {}
    cb = X.CreditBudget(s, u)
    t0 = datetime(2026, 11, 20, 15, 0)
    cb.add(999.5, t0)
    st1 = cb.status(t0)
    cb.add(0.5, t0)
    st2, nxt = cb.status(t0), cb.next_allowed(t0)
    cb.raise_limit(t0)
    st3 = cb.status(t0)
    t1 = datetime(2026, 12, 1, 0, 0)
    check("credit limit: ok -> reached -> raised half a limit for now -> ok; a new month starts from zero",
          (st1, st2, nxt, st3) == ("ok", "soft", 1500.0, "ok") and cb.used(t1) == 0 and cb.allowed(t1) == 1000
          and cb.reset_at(t0) == datetime(2026, 12, 1), str((st1, st2, nxt, st3)))
    check("credit limit: the default is 5,000 a month; 'no limit' never asks",
          X.CreditBudget({}, {}).limit == 5000 and X.CreditBudget({"credit_limit": 0}, {"used": 1e9}).status() == "ok")
    check("credits are shown like GitHub does", [X.fmt_credits(x) for x in (0, 0.04, 5, 28.1, 99.96, 5000, 20000)]
          == ["0", "0.04", "5", "28.1", "100", "5,000", "20,000"],
          str([X.fmt_credits(x) for x in (0, 0.04, 5, 28.1, 99.96, 5000, 20000)]))
    check("copilot: tags, names, the provider in the list",
          X.parse("copilot:claude-opus-5.5") == ("copilot", "claude-opus-5.5") and "copilot" in X.PROVIDERS
          and X.COPILOT.name == "GitHub" and X.COPILOT.brand == "Copilot")
    # effort, quota, history ---------------------------------------------------------------------------
    eff = [CC.pick_effort(True, "medium", ["low", "medium", "high"]), CC.pick_effort(True, "max", ["low", "high"]),
           CC.pick_effort(False, "high", ["low", "medium"]), CC.pick_effort(True, "low", ["medium", "high"]),
           CC.pick_effort(True, "high", [])]
    check("effort: the closest the model has (at or below), thinking off = its lowest, none = the model's own",
          eff == ["medium", "high", "low", "medium", None], str(eff))
    check("the quota that matters: AI credits before premium requests and chat",
          CC.pick_quota({"chat": 1, "premium_interactions": 2, "ai_credits": 3})[0] == "ai_credits"
          and CC.pick_quota({"chat": 1, "premium_interactions": 2})[0] == "premium_interactions")
    told = CC.compose([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Meow!"},
                       {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_time"}}]},
                       {"role": "tool", "tool_name": "get_time", "content": "12:00"},
                       {"role": "user", "content": "and now?"}])
    check("unseen history goes in as one message: earlier part as context, then the new message",
          told.startswith("(Earlier in this chat") and "User: hi" in told and "You: Meow!" in told
          and "[result of get_time] 12:00" in told and told.endswith("(Now:)\nand now?"), told.replace("\n", " | "))
    check("...just a new message stays as it is", CC.compose([{"role": "user", "content": "hello"}]) == "hello")

    # sign in with GitHub (device flow) ------------------------------------------------------------------
    GS.CLIENT_ID = ""
    hidden = not GS.available()
    GS.CLIENT_ID = "Iv-pixel"
    creds = X.Credentials(secrets=SECRETS)
    shown: list = []
    info = GS.sign_in(creds, lambda code, uri: shown.append((code, uri)), threading.Event())
    raw = (Path(os.environ["APPDATA"]) / "PixelCat" / "keys.dat").read_bytes()
    check("Sign in with GitHub: hidden without Pixel Cat's own client id; the code is shown, GitHub is polled until "
          "it's approved, the token is kept encrypted",
          hidden and shown == [("ABCD-1234", "https://github.com/login/device")] and info == {"login": "octocat"}
          and GS.signed_in(creds) and b"gho_fake" not in raw and S["device_polls"] == 2, str(shown))
    GS.sign_out(creds)
    check("...signing out forgets it", not GS.signed_in(creds))
    GS.CLIENT_ID = ""

    # the runtime ------------------------------------------------------------------------------------------
    steps: list = []
    have = CC.runtime_path() is not None
    t_inst = time.time()
    path = CC.install(lambda d, t: steps.append((d, t)))
    check("GitHub's Copilot runtime: set up in Pixel Cat's tools folder" + ("" if have else " (downloaded with progress)"),
          Path(path).is_file() and str(Path(path)).startswith(str(CC.tools_dir())) and (have or len(steps) > 5),
          f"{time.time() - t_inst:.1f}s, {len(steps)} progress steps")
    login, ms = CC.check_token("github_pat_fake")
    check("a token is tried on its own: the login and the models", login == "octocat" and [m["id"] for m in ms]
          == ["fake-model", "think-model"])

    chat = CC.CopilotChat()
    hist: list = [{"role": "user", "content": "hi there"}]
    S["script"] = [reply("Meow! Hello.", usage=(120, 8))]
    out = run_worker(chat, hist)
    b0 = S["bodies"][-1] if S["bodies"] else {}
    tools = sorted(t["function"]["name"] for t in b0.get("tools", []))
    check("text: streamed, then done; tokens counted", out["done"] == ("Meow! Hello.", []) and "".join(out["tokens"])
          == "Meow! Hello." and out["used"] == [("copilot", 128)], str(out["done"] or out["failed"]))
    check("the runtime uses the cat's own system prompt and offers only the cat's tools (no Copilot tools)",
          b0.get("messages", [{}])[0] == {"role": "system", "content": "You are Pixel, a test cat."}
          and tools == ["get_time", "look_at_screen"], str(tools))
    hist.append({"role": "assistant", "content": out["done"][0] if out["done"] else ""})

    hist.append({"role": "user", "content": "what time is it, and show me"})
    n0 = len(S["bodies"])
    S["script"] = [reply("Let me look.", [("call_1", "get_time", {}), ("call_2", "look_at_screen", {})]),
                   reply("It is noon and I see your screen.")]
    out = run_worker(chat, hist)
    calls = out["done"][1] if out["done"] else []
    check("tool calls: both come back to the app, in order, with ids (nothing runs by itself)",
          out["done"] is not None and out["done"][0] == "Let me look." and [c["name"] for c in calls]
          == ["get_time", "look_at_screen"] and [c["id"] for c in calls] == ["call_1", "call_2"]
          and len(S["bodies"]) == n0 + 1, str(out["done"] or out["failed"]))
    hist.append(assistant("Let me look.", calls))
    hist.append({"role": "tool", "tool_name": "get_time", "content": "12:00"})
    hist.append({"role": "tool", "tool_name": "look_at_screen", "content": "here it is", "images": [PNG]})
    out = run_worker(chat, hist)
    b2 = S["bodies"][-1]
    tool_msgs = [m for m in b2["messages"] if m.get("role") == "tool"]
    pic = any(isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url"
                                                         for p in m["content"]) for m in b2["messages"])
    check("results go back into the same turn (paired by id, the screenshot as a picture); then the answer",
          out["done"] == ("It is noon and I see your screen.", []) and [m.get("tool_call_id") for m in tool_msgs][-2:]
          == ["call_1", "call_2"] and pic and sum("what time is it, and show me" in t for t in user_texts(b2)) == 1,
          f"ids={[m.get('tool_call_id') for m in tool_msgs]} pic={pic} "
          f"roles={[(m.get('role'), str(m.get('content'))[:40]) for m in b2['messages'][-4:]]}")
    hist.append({"role": "assistant", "content": out["done"][0] if out["done"] else ""})

    # Stop while a tool waits, then a new message
    hist.append({"role": "user", "content": "what time is it?"})
    S["script"] = [reply("", [("call_3", "get_time", {})])]
    out = run_worker(chat, hist)
    calls = out["done"][1] if out["done"] else []
    hist.append(assistant("", calls))
    chat.abort_soon()                                   # (the user pressed Stop before the tool ran)
    time.sleep(1.0)
    hist.append({"role": "user", "content": "never mind, tell a joke"})
    S["script"] = [reply("Why did the cat sit on the computer? To keep an eye on the mouse!")]
    n0 = len(S["bodies"])
    out = run_worker(chat, hist)
    b3 = S["bodies"][-1] if len(S["bodies"]) > n0 else {}
    check("Stop while a tool waits: the turn is dropped; the next message starts a new one",
          bool(calls) and out["done"] is not None and "mouse" in out["done"][0] and len(S["bodies"]) == n0 + 1
          and user_texts(b3)[-1].endswith("never mind, tell a joke") and not chat.pending,
          str(out["done"] or out["failed"]))
    hist.append({"role": "assistant", "content": out["done"][0] if out["done"] else ""})

    # a model switch inside the chat keeps the conversation; effort for a thinking model
    hist.append({"role": "user", "content": "think hard: 2+2?"})
    S["script"] = [reply("4")]
    out = run_worker(chat, hist, model="think-model", effort="high")
    b4 = S["bodies"][-1]
    check("another Copilot model mid-chat: same conversation, the model and its effort change",
          out["done"] == ("4", []) and b4.get("model") == "think-model" and "never mind, tell a joke" in
          " ".join(user_texts(b4)) and chat.effort == "high", f"{b4.get('model')} {chat.effort} {b4.get('reasoning_effort')}")
    hist.append({"role": "assistant", "content": "4"})

    # errors
    kinds = []
    for st in (401, 402, 429, 500):
        hist.append({"role": "user", "content": f"error {st}"})
        S["script"] = [reply(f"fake {st}", status=st)] * 8
        out = run_worker(chat, hist, timeout=240)
        f = out["failed"] or ("", "")
        kinds.append(f[0] if f[0] != "limit" else "limit:" + json.loads(f[1]).get("why", ""))
        S["script"] = []
    check("errors: sign-in -> auth, allowance -> limit popup, rate limit -> rate popup, server -> busy",
          kinds == ["auth", "limit:copilot", "limit:rate", "busy"], str(kinds))

    # a chat that started elsewhere: the runtime gets what happened before
    chat2 = CC.CopilotChat()
    hist2 = [{"role": "user", "content": "my name is Anna"}, {"role": "assistant", "content": "Hi Anna!"},
             {"role": "user", "content": "what's my name?"}]
    S["script"] = [reply("Anna!")]
    out = run_worker(chat2, hist2)
    first = user_texts(S["bodies"][-1])[-1]
    check("a chat that began with another model: the runtime is told the earlier part",
          out["done"] == ("Anna!", []) and "User: my name is Anna" in first and "You: Hi Anna!" in first
          and first.endswith("what's my name?"), first.replace("\n", " | ")[:200])
    chat2.close()
    # Copilot's own web_fetch (Web search on): every page goes to the app first ----------------------------
    from pixelcat.llm import webpage as WP

    def tool_texts(body: dict) -> str:
        msgs = [m.get("content") for m in body.get("messages", []) if m.get("role") == "tool"]
        return json.dumps(msgs[-1:])                    # (the latest result)

    def ask(answer: bool, asked: list):
        return lambda url, reply_: (asked.append(url), reply_(answer))

    WP.ALLOW_LOCAL = True                               # (the test's page is on this PC)
    chat3 = CC.CopilotChat()
    hist3 = [{"role": "user", "content": "what does the cat page say?"}]
    asked: list = []
    S["script"] = [reply("", [("call_f", "web_fetch", {"url": BASE + "/page"})]), reply("It says cats nap.")]
    n0 = len(S["bodies"])
    out = run_worker(chat3, hist3, web=True, on_perm=ask(True, asked))
    tools0 = sorted(t["function"]["name"] for t in S["bodies"][n0].get("tools", [])) if len(S["bodies"]) > n0 else []
    check("web_fetch (Web search on): offered next to the cat's tools; the page is asked for first - approved, the "
          "runtime goes ahead (and its own guard refuses this PC's addresses too, so the test page isn't read)",
          tools0 == ["get_time", "look_at_screen", "web_fetch"] and asked == [BASE + "/page"]
          and out["done"] == ("It says cats nap.", []) and "blocked address" in tool_texts(S["bodies"][-1]),
          f"{tools0} {asked} {out['done'] or out['failed']} {tool_texts(S['bodies'][-1])[-300:]}")
    hist3 += [{"role": "assistant", "content": "It says cats nap."}, {"role": "user", "content": "read it again"}]
    asked2: list = []
    S["script"] = [reply("", [("call_g", "web_fetch", {"url": BASE + "/page?again"})]), reply("Okay, I won't.")]
    out = run_worker(chat3, hist3, web=True, on_perm=ask(False, asked2))
    check("...the user says no: not fetched, the model hears so", asked2 == [BASE + "/page?again"]
          and out["done"] == ("Okay, I won't.", []) and "blocked address" not in tool_texts(S["bodies"][-1])
          and "allow" in tool_texts(S["bodies"][-1]), tool_texts(S["bodies"][-1])[-200:])
    WP.ALLOW_LOCAL = False
    hist3 += [{"role": "assistant", "content": "Okay, I won't."}, {"role": "user", "content": "the local one?"}]
    asked3: list = []
    S["script"] = [reply("", [("call_h", "web_fetch", {"url": BASE + "/page?local"})]), reply("Can't, sorry.")]
    out = run_worker(chat3, hist3, web=True, on_perm=ask(True, asked3))
    check("...an address on this PC / the local network is refused without asking",
          asked3 == [] and out["done"] == ("Can't, sorry.", []) and "local network" in tool_texts(S["bodies"][-1]),
          f"{asked3} {tool_texts(S['bodies'][-1])[-200:]}")
    chat3.close()
    sid = chat.session_id
    chat.close()
    left = CC._LOOP.run(CC._RT.client.list_sessions(), 30) if CC._RT.client is not None else []
    check("a finished chat's session is deleted (the cat keeps no chats)", sid and all(x.session_id != sid for x in left),
          f"{len(left)} left")

    # in the app ---------------------------------------------------------------------------------------------
    app_checks()
    CC.shutdown()
    SRV.shutdown()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


def wait(cond, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def app_checks() -> None:  # noqa: C901 - a linear test
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
    from pixelcat.ui import dialogs
    dialogs.KeyDialog.show_near = lambda self, *a: None   # (never shown: no focus taken from the user)
    pet.creds = pet.accounts.creds = pet.provider_settings.creds = X.MemoryCredentials({"copilot_key": "github_pat_fake"})
    pet.accounts.ext_p("copilot").update(auth="key", login="octocat", limit=0)
    pet.accounts.set_models("copilot", MODELS)
    pet._copilot_quota(force=True)
    wait(lambda: isinstance(pet.accounts.ext_p("copilot").get("quota"), dict), 30)
    # the menu ---------------------------------------------------------------------------------------------
    items = {i.key: i for i in pet.menus.menu_items()}
    ai = next(c for c in items["settings"].children if c.key == "set_model")
    ext = next(c for c in ai.children if c.key == "ext")
    cop = next((c for c in ext.children if c.key == "ext_copilot"), None)
    keys = [c.key for c in cop.children] if cop else []
    labels = {c.key: c.label for c in cop.children} if cop else {}
    check("Settings > AI model > External AI models: Copilot (GitHub) next to Claude and ChatGPT - model, token, "
          "token limit, credit limit, the plan's allowance; no 'Sign in with GitHub' without a client id",
          [c.key for c in ext.children][:3] == ["ext_anthropic", "ext_openai", "ext_copilot"]
          and {"ext_models_copilot", "ext:key:copilot", "ext_limit_copilot", "ext_climit_copilot", "ext:quota:copilot",
               "ext:out:copilot"} <= set(keys) and "ext:signin:copilot" not in keys
          and labels.get("ext:state:copilot") == "Connected with a GitHub token (octocat)"
          and labels.get("ext:quota:copilot") == "Copilot plan: 3,120 of 20,000 used", str(labels))
    GS.CLIENT_ID = "Iv-pixel"
    keys2 = [c.key for c in pet.provider_settings.provider_menu("copilot").children]
    check("...with Pixel Cat's client id the sign-in shows up", "ext:signin:copilot" in keys2)
    pet._on_menu("ext:climit:copilot:2000")
    check("menu: the credit limit changes", pet._credit_budget().limit == 2000)
    # a custom credit limit (Credit limit -> Custom...), up to the plan's own credits ----------------------
    pet._on_menu("ext:ccustom:copilot")
    dlg = pet.provider_settings.key_dialog
    answers = []
    for text in ("plenty", "25000", "12.5k"):
        dlg.field.setText(text)
        dlg._save()
        wait(lambda: dlg.save.isEnabled() or pet._credit_budget().limit != 2000, 10)
        answers.append(dlg.status.text() if dlg.save.isEnabled() else "ok")
    climit = next(c for c in pet.provider_settings.provider_menu("copilot").children if c.key == "ext_climit_copilot").children
    custom = next(c for c in climit if c.key == "ext:ccustom:copilot")
    check("Credit limit -> Custom...: up to the plan's 20,000 credits - more is refused in the dialog; 12.5k is kept",
          "understand" in answers[0] and "20,000" in answers[1] and answers[2] == "ok"
          and pet._credit_budget().limit == 12500 and custom.checked and custom.label == "Custom: 12,500 credits"
          and custom.note == "UP TO 20,000", str(answers))
    q = pet.accounts.ext_p("copilot")["quota"]
    q["total"] = 3000.0                                   # (a smaller plan)
    labels = [c.label for c in next(c for c in pet.provider_settings.provider_menu("copilot").children
                                    if c.key == "ext_climit_copilot").children]
    check("...a smaller plan: no presets above it, the whole plan as the top choice",
          labels == ["No limit", "500 credits", "1,000 credits", "2,000 credits", "3,000 credits (your whole plan)",
                     "Custom: 12,500 credits"], str(labels))
    q["total"] = 20000.0
    pet._on_menu("ext:climit:copilot:2000")
    m = pet.menu
    m.items = pet.menus.menu_items()
    m.expanded = {"settings", "set_model", "ext", "ext_copilot"}
    m.u = 3
    w_, h_ = m._layout()
    m.resize(w_, h_)
    (ROOT / "build" / "preview").mkdir(parents=True, exist_ok=True)
    m.grab().save(str(ROOT / "build" / "preview" / "menu_copilot.png"))
    pet._on_menu("model:copilot:fake-model")
    check("picking a Copilot model makes it the default", pet.cfg.model == "copilot:fake-model"
          and X.label(pet.cfg.model) == "Fake Model (GitHub)")
    # chatting ---------------------------------------------------------------------------------------------
    pet._show_chat_quietly()
    chat = pet.chat

    def say(text: str) -> None:
        n = len(pet.popups.open)
        pet._on_user_message(text)
        wait(lambda: (pet.worker is None and not pet._task_active) or chat.choice.isVisible()
             or chat._confirm_cb is not None or len(pet.popups.open) > n, 90)

    S["script"] = [reply("Meow, hi!")]
    say("hi")
    reply1 = next((mm.text for mm in reversed(chat.msgs) if mm.role == "cat"), "")
    check("a chat with a Copilot model: the answer arrives; tokens are counted",
          "Meow, hi!" in reply1 and pet.accounts.budget("copilot").used() == 110, f"{reply1!r} {pet.accounts.budget('copilot').used()}")
    S["script"] = [reply("", [("call_t", "get_time", {})]), reply("It's time for a nap.")]
    say("what time is it?")
    reply2 = next((mm.text for mm in reversed(chat.msgs) if mm.role == "cat"), "")
    check("a tool that needs no yes runs, its result goes back, then the answer",
          "nap" in reply2 and any(mm.get("role") == "tool" and mm.get("tool_name") == "get_time" for mm in pet.history),
          repr(reply2))
    S["script"] = [reply("", [("call_u", "open_url", {"url": "https://example.com/"})]), reply("Okay, I won't.")]
    say("open example.com")
    asked = chat._confirm_cb is not None and chat.confirm.isVisible()
    if asked:
        chat._answer("no")                              # (declined: nothing opens)
    wait(lambda: not pet._task_active, 60)
    declined = any(mm.get("role") == "tool" and "declined" in str(mm.get("content")) for mm in pet.history)
    sent = any("declined" in json.dumps(b.get("messages", [])) for b in S["bodies"][-1:])
    check("a tool that needs a yes is asked in the chat (as with every model) - declined, the model hears so",
          asked and declined and sent, f"asked={asked} declined={declined} sent={sent}")
    # Copilot's web_fetch asks through the app (like read_web_page) ------------------------------------------
    answers: list = []
    pet._task_active = True
    pet._copilot_permission("https://example.com/a", answers.append)
    card1 = chat._confirm_cb is not None and "let Copilot read the web page https://example.com/a" in \
        chat.confirm_label.text()
    chat._answer("no")
    pet._copilot_permission("https://example.com/b", answers.append)
    chat._answer("always")
    pet._copilot_permission("https://example.com/c", answers.append)
    card3 = chat._confirm_cb is not None
    pet._task_active = False
    pet._copilot_permission("https://example.com/d", answers.append)
    check("Copilot wants a page: the confirmation card (no -> not read; ALWAYS -> 'web' is remembered and the next "
          "page is read without asking - like read_web_page); no running task -> refused",
          card1 and answers == [False, True, True, False] and not card3 and "web" in saved.auto_approved_tools,
          str(answers))
    saved.auto_approved_tools.remove("web")
    # the credit limit ------------------------------------------------------------------------------------------
    pet._on_copilot_credits(1999.0)
    pet._on_copilot_credits(1.0)
    S["script"] = [reply("Still here!")]
    pet._on_user_message("are you there?")
    wait(lambda: chat.choice.isVisible(), 20)
    card = chat.choice_label.text() if chat.choice.isVisible() else ""
    labels = [b_.text() for b_ in chat._choice_btns] if chat.choice.isVisible() else []
    check("credit limit reached: the chat asks - SWITCH MODEL / RAISE TO 3,000 / STOP, with the plan's own counter",
          labels == ["SWITCH MODEL", "RAISE TO 3,000", "STOP"] and "Copilot credit limit reached" in card
          and "2,000" in card and "3,120 of 20,000" in card, f"{labels} {card[:160]}")
    if labels:
        chat._choice_btns[1].click()
    wait(lambda: not pet._task_active, 60)
    reply3 = next((mm.text for mm in reversed(chat.msgs) if mm.role == "cat"), "")
    check("RAISE: the limit is raised until the month ends, and the request goes on",
          pet._credit_budget().allowed() == 3000 and "Still here!" in reply3, f"{pet._credit_budget().allowed()} {reply3!r}")
    # the plan's allowance used up -------------------------------------------------------------------------------
    pet.accounts.ext_p("copilot")["credit_limit"] = 0
    S["script"] = [reply("allowance", status=402)] * 6
    n_pop = len(pet.popups.open)
    pet._on_user_message("one more?")
    wait(lambda: len(pet.popups.open) > n_pop, 120)
    pop = pet.popups.open[-1] if len(pet.popups.open) > n_pop else None
    text = pop.text.text() if pop is not None else ""
    check("Copilot's own allowance used up: a popup that says when it resets", pop is not None
          and pop.title == "Copilot allowance used up" and "Dec 1" in text, text[:160])
    if pop is not None:
        pop._finish(False)
    wait(lambda: not pet._task_active)
    S["script"] = []
    # new chat, quit -------------------------------------------------------------------------------------------------
    sid = pet._copilot.session_id if pet._copilot is not None else ""
    pet.new_chat()
    time.sleep(2.0)
    left = CC._LOOP.run(CC._RT.client.list_sessions(), 30) if CC._RT.client is not None else []
    check("new chat: the Copilot session is deleted", bool(sid) and pet._copilot is None
          and all(x.session_id != sid for x in left))
    proc = getattr(CC._RT.client, "_cli_process", None)
    pet._stop_copilot()
    gone = proc is not None and proc.poll() is not None
    check("quit / sign-out: GitHub's runtime stops", CC._RT.client is None and gone,
          f"pid {getattr(proc, 'pid', None)} exit {proc.poll() if proc is not None else None}")
    chat.hide()


if __name__ == "__main__":
    sys.exit(main())
