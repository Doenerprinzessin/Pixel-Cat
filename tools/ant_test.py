"""One-click "Sign in with Anthropic" test (developer tool).  APPDATA and LOCALAPPDATA are
moved to build/anttest; a fake GitHub and a fake ``ant`` stand in for the real ones, so
nothing is signed in anywhere.  With --live the real helper is downloaded from Anthropic's
GitHub releases into build/anttest (checked, unpacked, started with --version - no sign-in).

* Download: checked against GitHub's SHA-256 and the release's checksums file; a wrong
  checksum, checksums that disagree, a download from elsewhere, no Windows build -> refused,
  nothing kept.  Only ant.exe is unpacked, into Pixel Cat's own tools folder.
* Sign-in: the sign-in page opens in the browser (no window, no code to paste); it lands in
  Pixel Cat's own folder (ANTHROPIC_CONFIG_DIR for that process only, API keys from the
  environment not passed on); the SDK gets it as its profile config; cancel; sign out.
* In the app: SIGN IN -> helper fetched -> browser -> Claude connected (its models, the
  sign-in's token used) -> sign out.
Usage:  python tools/ant_test.py [--live]
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "build" / "anttest"
shutil.rmtree(TEST, ignore_errors=True)
(TEST / "AppData").mkdir(parents=True)
(TEST / "Local").mkdir(parents=True)
os.environ["APPDATA"] = str(TEST / "AppData")
os.environ["LOCALAPPDATA"] = str(TEST / "Local")
for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR"):
    os.environ.pop(k, None)
os.environ["ANTHROPIC_API_KEY"] = "sk-must-not-reach-ant"     # (ant must not get it)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelcat.llm import ant_tool as T  # noqa: E402
from pixelcat.llm import external as X  # noqa: E402

app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def wait(cond, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ------------------------------------------------------------------ a fake ant (a .cmd running Python)
FAKE = TEST / "fakeant"
FAKE.mkdir()
APPROVE = FAKE / "approved"
(FAKE / "fake_ant.py").write_text(r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
here = Path(__file__).parent
if args[:1] == ["--version"]:
    print("ant version 9.9.9"); sys.exit(0)
d = Path(os.environ["ANTHROPIC_CONFIG_DIR"])
if args[:2] == ["auth", "login"]:
    (here / "env.json").write_text(json.dumps({"api_key": os.environ.get("ANTHROPIC_API_KEY"), "dir": str(d),
                                               "args": args}))
    print('Creating profile "default".')
    print("Authorization URL (open on the machine where your SSH session originates):")
    print("")
    print("  https://platform.claude.com/oauth/authorize?client_id=x&state=s", flush=True)
    end = time.time() + 15
    while not (here / "approved").exists():
        if time.time() > end:
            print("Error: timed out waiting for authorization"); sys.exit(1)
        time.sleep(0.05)
    (d / "configs").mkdir(parents=True, exist_ok=True)
    (d / "credentials").mkdir(parents=True, exist_ok=True)
    (d / "configs" / "default.json").write_text(json.dumps({"authentication": {"type": "user_oauth"}}))
    (d / "credentials" / "default.json").write_text(json.dumps({"access_token": "tok-from-ant",
                                                                "expires_at": int(time.time()) + 3600}))
    (d / "active_config").write_text("default")
    print("✓ Logged in as profile \"default\".", flush=True)
    sys.exit(0)
if args[:2] == ["auth", "logout"]:
    (d / "credentials" / "default.json").unlink(missing_ok=True)
    (here / "logged_out").write_text("yes")
    sys.exit(0)
sys.exit(2)
''', encoding="utf-8")
FAKE_EXE = FAKE / "ant.cmd"
FAKE_EXE.write_text(f'@set PYTHONIOENCODING=utf-8\n@"{sys.executable}" "{FAKE / "fake_ant.py"}" %*\n', encoding="utf-8")


# ------------------------------------------------------------------ a fake GitHub (+ the models endpoint)
def make_zip(content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LICENSE", "license")
        z.writestr("completions/ant.bash", "#")
        z.writestr("ant.exe", content)
    return buf.getvalue()


ZIP = make_zip(b"FAKE-ANT-EXE" * 1000)
S: dict = {"digest": "sha256:" + hashlib.sha256(ZIP).hexdigest(), "sums": hashlib.sha256(ZIP).hexdigest(),
           "arch_name": None, "auth": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *_a) -> None:
        pass

    def send(self, code: int, data: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        base = f"http://127.0.0.1:{SRV.server_address[1]}/gh/"
        name = S["arch_name"] or f"ant_9.9.9_windows_{T._arch()}.zip"
        if self.path == "/gh/releases/latest":
            return self.send(200, json.dumps({"tag_name": "v9.9.9", "assets": [
                {"name": name, "size": len(ZIP), "digest": S["digest"], "browser_download_url": base + name},
                {"name": "ant_9.9.9_checksums.txt", "size": 100, "browser_download_url": base + "sums.txt"},
            ]}).encode())
        if self.path.endswith(".zip"):
            return self.send(200, ZIP, "application/zip")
        if self.path.endswith("sums.txt"):
            return self.send(200, f"{S['sums']}  {name}\nabc  other.zip\n".encode(), "text/plain")
        if self.path.startswith("/v1/models"):
            S["auth"].append(self.headers.get("Authorization", ""))
            return self.send(200, json.dumps({"data": [
                {"type": "model", "id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5",
                 "created_at": "2026-09-01T00:00:00Z", "max_input_tokens": 1000000, "max_tokens": 128000,
                 "capabilities": {}}], "has_more": False, "first_id": "claude-sonnet-5-5",
                "last_id": "claude-sonnet-5-5"}).encode())
        self.send(404, b"{}")


SRV = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"


def main() -> int:  # noqa: C901 - a linear test script
    real_check, real_is_ant = T._check_url, T._is_ant
    for bad in ("https://evil.example.com/ant.zip", "http://github.com/anthropics/anthropic-cli/x.zip"):
        try:
            T._check_url(bad)
            refused = False
        except T.AntError:
            refused = True
        check(f"a download from {bad.split('/')[2]} over {bad.split(':')[0]} is refused", refused)
    T._check_url("https://objects.githubusercontent.com/github-production-release-asset/x")
    check("...GitHub's download hosts are fine", True)

    if "--live" in sys.argv:
        rel = T.latest()
        t0 = time.time()
        got: list[tuple[int, int]] = []
        exe = T.install(lambda d, t: got.append((d, t)))
        check("LIVE: the real helper from Anthropic's GitHub: checked, unpacked, it starts",
              Path(exe) == T.tool_path() and T._is_ant(exe) and got and got[-1][0] == got[-1][1],
              f"{rel['name']}, {got[-1][0] / 1e6:.1f} MB in {time.time() - t0:.0f} s")
        shutil.rmtree(T.tool_path().parent, ignore_errors=True)

    # downloading (fake GitHub) --------------------------------------------------------------
    T.RELEASES = BASE + "/gh/releases/latest"
    T.DOWNLOAD_PREFIX = BASE + "/gh/"
    T._check_url = lambda url: None
    T._is_ant = lambda exe: Path(exe).read_bytes().startswith(b"FAKE-ANT")
    steps: list[tuple[int, int]] = []
    exe = T.install(lambda d, t: steps.append((d, t)))
    left = sorted(p.name for p in T.tool_path().parent.iterdir())
    check("the helper is downloaded with progress, checked, and only ant.exe is unpacked into Pixel Cat's folder",
          Path(exe) == T.tool_path() and left == ["ant.exe"] and steps[-1] == (len(ZIP), len(ZIP))
          and str(T.tool_path()).startswith(str(TEST / "Local" / "PixelCat" / "tools")), str(left))
    check("find() finds Pixel Cat's own copy", T.find() == str(T.tool_path()))

    def refused_with(**change) -> str:
        shutil.rmtree(T.tool_path().parent, ignore_errors=True)
        old = dict(S)
        S.update(change)
        try:
            T.install()
            msg = "installed!"
        except T.AntError as e:
            msg = str(e)
        S.update(old)
        return msg
    msg = refused_with(digest="sha256:" + "0" * 64, sums="0" * 64)
    check("a download that doesn't match its checksum is thrown away", "checksum" in msg
          and not T.tool_path().exists(), msg)
    msg = refused_with(sums="1" * 64)
    check("GitHub's checksum and the checksums file disagree -> not used", "couldn't confirm" in msg
          and not T.tool_path().exists(), msg)
    msg = refused_with(arch_name="ant_9.9.9_linux_amd64.tar.gz")
    check("no Windows build -> said so", "no Windows version" in msg, msg)

    # signing in (fake ant) --------------------------------------------------------------------
    opened: list[str] = []

    def browser(url: str) -> None:
        opened.append(url)
        APPROVE.write_text("ok")
    T.login(str(FAKE_EXE), browser)
    env = json.loads((FAKE / "env.json").read_text())
    cfg = T.client_config()
    check("sign in: the sign-in page opens in the browser; ant waits for the answer itself (no code to paste)",
          opened == ["https://platform.claude.com/oauth/authorize?client_id=x&state=s"]
          and "--no-browser" in env["args"] and "--callback-port" in env["args"], str(env["args"]))
    check("...the sign-in lands in Pixel Cat's own folder - API keys from the environment aren't passed to ant",
          env["dir"] == str(T.signin_dir()) and env["api_key"] is None
          and (T.signin_dir() / "credentials" / "default.json").is_file())
    check("...the SDK gets it as its profile config (with the token file's full path)",
          cfg is not None and cfg["authentication"]["credentials_path"] == str(T.signin_dir() / "credentials"
                                                                              / "default.json"))
    import anthropic
    X.BASE_URLS["anthropic"] = BASE
    from pixelcat.llm import anthropic_client as AC
    ms = AC.list_models(X.Credentials(), "signin")
    check("...and the requests use the sign-in's token", [m["id"] for m in ms] == ["claude-sonnet-5-5"]
          and S["auth"][-1] == "Bearer tok-from-ant", S["auth"][-1][:20])
    APPROVE.unlink()
    cancel = threading.Event()
    threading.Timer(0.8, cancel.set).start()
    t0 = time.time()
    try:
        T.login(str(FAKE_EXE), lambda url: None, cancel)
        msg = "finished!"
    except T.AntError as e:
        msg = str(e)
    check("cancelling stops the sign-in at once", "cancelled" in msg and time.time() - t0 < 5, msg)
    T.logout(str(FAKE_EXE))
    check("sign out: ant signs the profile out, Pixel Cat's sign-in folder is gone",
          (FAKE / "logged_out").exists() and not T.signin_dir().exists())
    try:
        AC.client(X.Credentials(), "signin")
        msg = "made a client"
    except anthropic.CredentialsError as e:
        msg = str(e)
    check("...after that, requests say 'sign in again' instead of using anything else", "sign in again" in msg, msg)

    # in the app ----------------------------------------------------------------------------------
    app_flow()
    SRV.shutdown()
    T._check_url, T._is_ant = real_check, real_is_ant
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


def app_flow() -> None:
    import webbrowser

    from pixelcat import persistence
    from pixelcat.app import PixelCatApp
    from pixelcat.bootstrap import build_platform
    from pixelcat.adapters.windows import win32
    from pixelcat.logsetup import setup_logging
    from pixelcat.settings import load_config

    setup_logging(False)
    win32.set_dpi_awareness()
    pet = PixelCatApp(app, load_config(), persistence.SavedState(autostart_asked=True), selftest=1,
                      platform=build_platform())
    pet.save = lambda: None
    shutil.rmtree(T.tool_path().parent, ignore_errors=True)
    have = {"exe": None}
    T.find = lambda: have["exe"]
    installs: list[int] = []

    def fake_install(progress=None, cancel=None) -> str:
        installs.append(1)
        if progress:
            progress(5, 10)
            progress(10, 10)
        have["exe"] = str(FAKE_EXE)
        return str(FAKE_EXE)
    T.install = fake_install
    opened: list[str] = []
    webbrowser.open = lambda url, *a, **k: (opened.append(url), APPROVE.write_text("ok"))
    n = len(pet._popups)
    pet._anthropic_signin()
    wait(lambda: len(pet._popups) > n)
    ask = pet._popups[-1]
    text = ask.text.text()
    check("app: Sign in with Anthropic asks once, in plain words (helper, browser, Allow, billing, privacy)",
          ask.title == "Sign in with Anthropic" and "sign-in helper" in text and "Allow" in text
          and "Console" in text and "Anthropic" in text, text[:120])
    ask._finish(True)
    wait(lambda: pet._ext_auth("anthropic") == "signin" and pet._ext_models("anthropic"), 30)
    connected = [p for p in pet._popups if p.title == "Claude is connected"]
    check("...one click: the helper is fetched, the browser opens, and Claude is connected with its models",
          installs == [1] and opened and pet._ext_auth("anthropic") == "signin"
          and [m["id"] for m in pet._ext_models("anthropic")] == ["claude-sonnet-5-5"] and bool(connected)
          and S["auth"][-1] == "Bearer tok-from-ant", f"{installs} {opened[:1]} {pet._ext_auth('anthropic')}")
    for p in list(pet._popups):
        try:
            p._finish(False)
        except RuntimeError:
            pass
    n = len(pet._popups)
    pet._anthropic_signin()
    wait(lambda: len(pet._popups) > n)
    again = pet._popups[-1].text.text() if len(pet._popups) > n else ""
    check("...next time (helper already here) it doesn't mention fetching it", "sign-in helper" not in again
          and "Allow" in again)
    pet._popups[-1]._finish(False)
    (FAKE / "logged_out").unlink(missing_ok=True)
    pet._ext_disconnect("anthropic")
    wait(lambda: (FAKE / "logged_out").exists() and not T.signin_dir().exists())
    check("app: Sign out signs Pixel Cat's profile out and forgets it", pet._ext_auth("anthropic") == ""
          and (FAKE / "logged_out").exists() and not T.signin_dir().exists())
    for p in list(pet._popups):
        try:
            p._finish(False)
        except RuntimeError:
            pass


if __name__ == "__main__":
    sys.exit(main())
