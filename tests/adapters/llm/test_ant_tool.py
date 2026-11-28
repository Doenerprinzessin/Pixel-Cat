"""ant_tool: Anthropic's sign-in helper - where it lives, finding it, the checked download from
GitHub (a fake GitHub on 127.0.0.1), signing in and out (the process is faked), and the
profile the SDK gets."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.llm.fakes import Json, Raw
from pixelcat import paths
from pixelcat.adapters.llm import ant_tool as T


@pytest.fixture(autouse=True)
def mac(monkeypatch):
    """The Windows build by default (the tests run on a Mac too); ``mac()`` switches to the macOS one."""
    monkeypatch.setattr(T, "_macos", lambda: False)
    return lambda: monkeypatch.setattr(T, "_macos", lambda: True)


def test_no_window_flag_is_the_windows_one_or_zero():
    assert T.NO_WINDOW == getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_folders():
    assert T.tool_path() == Path(os.environ["LOCALAPPDATA"]) / "PixelCat" / "tools" / "ant" / "ant.exe"
    assert T.signin_dir() == paths.data_dir() / "anthropic"


def test_folders_on_a_mac(mac):
    mac()
    assert T.tool_path() == paths.local_data_dir() / "PixelCat" / "tools" / "ant" / "ant"


@pytest.mark.parametrize("machine, arch", [("AMD64", "amd64"), ("x86_64", "amd64"), ("ARM64", "arm64"),
                                           ("aarch64", "arm64"), ("i686", "386"), ("x86", "386"), ("riscv", "amd64")])
def test_arch(monkeypatch, machine, arch):
    monkeypatch.setattr(T.platform, "machine", lambda: machine)
    assert T._arch() == arch


# ------------------------------------------------------------------ is it Anthropic's ant?
@pytest.fixture
def runs(monkeypatch):
    """subprocess.run in ant_tool: ``runs.answer`` (CompletedProcess or exception), ``runs.calls``."""
    r = SimpleNamespace(calls=[], answer=subprocess.CompletedProcess([], 0, "ant version 1.4.0\n", ""))

    def run(args, **kw):
        r.calls.append((args, kw))
        if isinstance(r.answer, BaseException):
            raise r.answer
        return r.answer
    monkeypatch.setattr(T.subprocess, "run", run)
    return r


@pytest.mark.parametrize("answer, ok", [
    (subprocess.CompletedProcess([], 0, "ant version 1.4.0 (go1.25)\n", ""), True),
    (subprocess.CompletedProcess([], 0, "", "ANT VERSION 2\n"), True),
    (subprocess.CompletedProcess([], 0, "Apache Ant(TM) version 1.10.14\n", ""), False),
    (subprocess.CompletedProcess([], 0, "ant version 1 - apache\n", ""), False),
    (subprocess.CompletedProcess([], 1, "ant version 1.4.0\n", ""), False),
    (FileNotFoundError("gone"), False),
    (subprocess.TimeoutExpired("ant", 20), False),
])
def test_is_ant(runs, answer, ok):
    runs.answer = answer
    assert T._is_ant("/x/ant.exe") is ok
    args, kw = runs.calls[0]
    assert args == ["/x/ant.exe", "--version"] and kw["timeout"] == 20 and kw["creationflags"] == T.NO_WINDOW


def test_find_prefers_pixel_cats_own_copy(monkeypatch, tmp_path):
    own = T.tool_path()
    own.parent.mkdir(parents=True)
    own.write_bytes(b"x")
    other = tmp_path / "ant"
    other.write_bytes(b"x")
    monkeypatch.setattr(T.shutil, "which", lambda name: str(other))
    monkeypatch.setattr(T, "_is_ant", lambda exe: True)
    assert T.find() == str(own)


def test_find_skips_apache_ant_and_missing_files(monkeypatch, tmp_path):
    apache = tmp_path / "apache" / "ant"
    apache.parent.mkdir()
    apache.write_bytes(b"x")
    gobin = Path.home() / "go" / "bin" / "ant.exe"
    gobin.parent.mkdir(parents=True)
    gobin.write_bytes(b"x")
    monkeypatch.setattr(T.shutil, "which", lambda name: str(apache))
    tried: list[str] = []
    monkeypatch.setattr(T, "_is_ant", lambda exe: tried.append(exe) or exe == str(gobin))
    assert T.find() == str(gobin)
    assert tried == [str(apache), str(gobin)]


def test_find_nothing(monkeypatch):
    monkeypatch.setattr(T.shutil, "which", lambda name: None)
    monkeypatch.setattr(T, "_is_ant", lambda exe: True)
    assert T.find() is None


@pytest.mark.parametrize("url, ok", [
    ("https://github.com/anthropics/anthropic-cli/releases/download/v1/ant.zip", True),
    ("https://objects.githubusercontent.com/github-production-release-asset/x", True),
    ("https://release-assets.githubusercontent.com/x", True),
    ("http://github.com/anthropics/anthropic-cli/x.zip", False),
    ("https://evil.example.com/ant.zip", False),
    ("https://github.com.evil.example/x", False),
])
def test_check_url(url, ok):
    if ok:
        T._check_url(url)
    else:
        with pytest.raises(T.AntError, match="didn't come from Anthropic's GitHub page"):
            T._check_url(url)


# ------------------------------------------------------------------ the download
def make_zip(*members: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


EXE = b"FAKE-ANT-EXE" * 1000
ZIP = make_zip(("LICENSE", b"license"), ("completions/ant.bash", b"#"), ("ant.exe", EXE))


class GitHub:
    def __init__(self, http, monkeypatch) -> None:
        self.http = http
        self.base = http.url + "/gh/"
        self.zip = ZIP
        self.name = f"ant_9.9.9_windows_{T._arch()}.zip"
        self.digest: str | None = "sha256:" + hashlib.sha256(ZIP).hexdigest()
        self.sums: str | None = hashlib.sha256(ZIP).hexdigest()
        self.url = self.base + self.name
        self.sums_url = self.base + "sums.txt"
        monkeypatch.setattr(T, "RELEASES", http.url + "/gh/releases/latest")
        monkeypatch.setattr(T, "DOWNLOAD_PREFIX", self.base)
        http.on("GET", "/gh/releases/latest", self.release)
        http.on("GET", "/gh/" + self.name, lambda req: Raw(200, self.zip, {"Content-Type": "application/zip"}))
        http.on("GET", "/gh/sums.txt", lambda req: Raw(200, f"{self.sums}  {self.name}\nabc  other.zip\n"
                                                       f"{'f' * 64}  *other_{self.name}\n".encode()))

    def release(self, req) -> Json:
        assets = [{"name": self.name, "size": len(self.zip), "browser_download_url": self.url,
                   **({"digest": self.digest} if self.digest else {})}]
        if self.sums is not None:
            assets.append({"name": "ant_9.9.9_checksums.txt", "size": 100, "browser_download_url": self.sums_url})
        return Json(200, {"tag_name": "v9.9.9", "assets": assets + ["junk"]})


@pytest.fixture
def github(fake_http, monkeypatch):
    return GitHub(fake_http, monkeypatch)


def test_latest(github):
    assert T.latest() == {"name": github.name, "url": github.url, "size": len(ZIP),
                          "sha256": hashlib.sha256(ZIP).hexdigest(), "version": "9.9.9"}
    req = github.http.sent("/gh/releases/latest")[0]
    assert req.header("accept") == "application/vnd.github+json" and req.header("user-agent") == "PixelCat"


@pytest.mark.parametrize("digest, sums", [("sha256:" + hashlib.sha256(ZIP).hexdigest(), None),
                                          (None, hashlib.sha256(ZIP).hexdigest().upper())])
def test_latest_with_one_checksum(github, digest, sums):
    github.digest, github.sums = digest, sums
    assert T.latest()["sha256"] == hashlib.sha256(ZIP).hexdigest()


@pytest.mark.parametrize("change, message", [
    ({"sums": "1" * 64}, "couldn't confirm the download's checksum"),
    ({"digest": None, "sums": None}, "couldn't confirm the download's checksum"),
    ({"digest": "md5:abc", "sums": "not-a-sum"}, "couldn't confirm the download's checksum"),
    ({"name": "ant_9.9.9_linux_amd64.tar.gz"}, "no Windows version"),
    ({"url": "https://evil.example.com/ant.zip"}, "didn't come from Anthropic's GitHub page"),
    ({"sums_url": "https://evil.example.com/sums.txt"}, "checksums didn't come from Anthropic's GitHub page"),
])
def test_latest_refused(github, change, message):
    for k, v in change.items():
        setattr(github, k, v)
    with pytest.raises(T.AntError, match=message):
        T.latest()


@pytest.mark.parametrize("answer, message", [
    (Json(403, {"message": "rate limited"}), "didn't answer properly"),
    (Json(200, ["a", "list"]), "didn't answer properly"),
    (Raw(200, b"<html>"), "couldn't reach Anthropic's download page"),
])
def test_latest_bad_answers(github, answer, message):
    github.http.on("GET", "/gh/releases/latest", answer)
    with pytest.raises(T.AntError, match=message):
        T.latest()


def test_latest_offline(monkeypatch):
    def get(*a, **kw):
        raise T.requests.ConnectionError("down")
    monkeypatch.setattr(T.requests, "get", get)
    with pytest.raises(T.AntError, match="is the internet on"):
        T.latest()


@pytest.fixture
def unpack(github, monkeypatch, tmp_path):
    """install() against the fake GitHub: any http host, ant.exe 'starts' if it is the fake one, and the
    download's temporary folder is inside the test's folder (``unpack.tmp``)."""
    monkeypatch.setattr(T, "_check_url", lambda url: None)
    monkeypatch.setattr(T, "_is_ant", lambda exe: Path(exe).read_bytes().startswith(b"FAKE-ANT"))
    tmp = tmp_path / "download"
    monkeypatch.setattr(T.tempfile, "mkdtemp", lambda prefix="": (tmp.mkdir(), str(tmp))[1])
    github.tmp = tmp
    return github


def test_install(unpack):
    steps: list[tuple[int, int]] = []
    exe = T.install(lambda d, t: steps.append((d, t)))
    assert exe == str(T.tool_path())
    assert T.tool_path().read_bytes() == EXE
    assert sorted(p.name for p in T.tool_path().parent.iterdir()) == ["ant.exe"]
    assert steps[-1] == (len(ZIP), len(ZIP))
    assert not unpack.tmp.exists()


def test_latest_and_install_on_a_mac(unpack, mac):
    mac()
    unpack.name = f"ant_9.9.9_macos_{T._arch()}.zip"
    unpack.zip = make_zip(("LICENSE", b"license"), ("man/man1/ant.1.gz", b"#"), ("ant", EXE))
    digest = hashlib.sha256(unpack.zip).hexdigest()
    unpack.digest, unpack.sums = "sha256:" + digest, digest
    unpack.url = unpack.base + unpack.name
    unpack.http.on("GET", "/gh/" + unpack.name, lambda req: Raw(200, unpack.zip, {}))
    assert T.latest()["name"] == unpack.name
    exe = T.install()
    assert exe == str(T.tool_path()) and Path(exe).name == "ant" and Path(exe).read_bytes() == EXE
    assert sorted(p.name for p in T.tool_path().parent.iterdir()) == ["ant"]
    if os.name != "nt":
        assert os.stat(exe).st_mode & 0o777 == 0o755


def test_latest_on_a_mac_wants_the_mac_build(github, mac):
    mac()
    with pytest.raises(T.AntError, match="no Mac version"):
        T.latest()                                     # (the fake release has the Windows zip only)


def test_find_on_a_mac_looks_for_ant(monkeypatch, mac):
    mac()
    gobin = Path.home() / "go" / "bin" / "ant"
    gobin.parent.mkdir(parents=True)
    gobin.write_bytes(b"x")
    monkeypatch.setattr(T.shutil, "which", lambda name: None)
    monkeypatch.setattr(T, "_is_ant", lambda exe: True)
    assert T.find() == str(gobin)


@pytest.mark.parametrize("change, message", [
    ({"zip": make_zip(("ant.exe", b"OTHER"))}, "doesn't match Anthropic's checksum"),
    ({"zip": make_zip(("readme", b"x")), "fix_sums": True}, "didn't contain the sign-in helper"),
    ({"zip": make_zip(("a/ant.exe", EXE), ("b/ANT.EXE", EXE)), "fix_sums": True}, "didn't contain the sign-in helper"),
    ({"zip": make_zip(("ant.exe", b"NOT-ANT")), "fix_sums": True}, "didn't start properly"),
])
def test_install_refused(unpack, change, message):
    unpack.zip = change["zip"]
    if change.get("fix_sums"):
        digest = hashlib.sha256(unpack.zip).hexdigest()
        unpack.digest, unpack.sums = "sha256:" + digest, digest
    with pytest.raises(T.AntError, match=message):
        T.install()
    assert not T.tool_path().exists() and not unpack.tmp.exists()


def test_install_http_error(unpack):
    unpack.http.on("GET", "/gh/" + unpack.name, Raw(404, b"gone"))
    with pytest.raises(T.AntError, match=r"HTTP 404"):
        T.install()


def test_install_cancelled_and_too_big(unpack, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(T.AntError, match="Cancelled"):
        T.install(cancel=cancel)
    monkeypatch.setattr(T, "MAX_ZIP", 100)
    with pytest.raises(T.AntError, match="far too big"):
        T.install()
    assert not T.tool_path().exists()


def test_install_only_from_github(github, monkeypatch, tmp_path):
    monkeypatch.setattr(T.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    with pytest.raises(T.AntError, match="didn't come from Anthropic's GitHub page"):
        T.install()                                    # (the real check: the fake GitHub is http on this PC)


def test_install_disk_trouble(unpack, monkeypatch):
    real_replace = os.replace

    def replace(src, dst):
        if str(dst).endswith("ant.exe"):
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)
    monkeypatch.setattr(T.os, "replace", replace)
    with pytest.raises(T.AntError, match=r"couldn't save the sign-in helper \(Access is denied\)"):
        T.install()


# ------------------------------------------------------------------ signing in and out
def write_profile(name: str = "default", creds_path: str | None = None, active: bool = True) -> Path:
    d = T.signin_dir()
    (d / "configs").mkdir(parents=True, exist_ok=True)
    (d / "credentials").mkdir(parents=True, exist_ok=True)
    auth: dict[str, Any] = {"type": "user_oauth"}
    if creds_path:
        auth["credentials_path"] = creds_path
    (d / "configs" / f"{name}.json").write_text(json.dumps({"authentication": auth, "base_url": "x"}))
    token = Path(creds_path).expanduser() if creds_path else d / "credentials" / f"{name}.json"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(json.dumps({"access_token": "tok"}))
    if active:
        (d / "active_config").write_text(name + "\n")
    return token


def test_client_config_default_profile():
    token = write_profile(active=False)
    assert T.client_config() == {"authentication": {"type": "user_oauth", "credentials_path": str(token)},
                                 "base_url": "x"}


def test_client_config_active_profile_with_its_own_token_file():
    token = write_profile("work", creds_path="~/tokens/work.json")
    cfg = T.client_config()
    assert cfg["authentication"]["credentials_path"] == str(token) == str(Path.home() / "tokens" / "work.json")


@pytest.mark.parametrize("breakage", ["nothing", "bad-name", "no-token", "bad-json", "no-auth", "not-a-dict"])
def test_client_config_missing_or_broken(breakage):
    d = T.signin_dir()
    if breakage != "nothing":
        token = write_profile()
    if breakage == "bad-name":
        (d / "active_config").write_text("../evil")
    elif breakage == "no-token":
        token.unlink()
    elif breakage == "bad-json":
        (d / "configs" / "default.json").write_text("{broken")
    elif breakage == "no-auth":
        (d / "configs" / "default.json").write_text(json.dumps({"base_url": "x"}))
    elif breakage == "not-a-dict":
        (d / "configs" / "default.json").write_text("[1]")
    assert T.client_config() is None


class FakeProc:
    def __init__(self, lines: list[str], code: int, on_exit: Any = None, polls: int = 2) -> None:
        self.stdout = iter(line + "\n" for line in lines)
        self.code, self.on_exit, self.polls = code, on_exit, polls
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        self.polls -= 1
        if self.polls > 0 and not self.killed:
            return None
        if self.returncode is None:
            if self.on_exit and not self.killed:
                self.on_exit()
            self.returncode = -9 if self.killed else self.code
        return self.returncode

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return self.poll() or 0


@pytest.fixture
def ant_proc(monkeypatch):
    """subprocess.Popen in ant_tool returns ``ant_proc.proc``; ``ant_proc.args`` / ``.kw`` are recorded."""
    box = SimpleNamespace(proc=None, args=None, kw=None, slept=[])

    def popen(args, **kw):
        box.args, box.kw = args, kw
        return box.proc
    monkeypatch.setattr(T.subprocess, "Popen", popen)
    monkeypatch.setattr(T, "time", SimpleNamespace(monotonic=time.monotonic, sleep=box.slept.append))
    return box


LOGIN_OUT = ['Creating profile "default".', "Authorization URL (open on the machine where your SSH session originates):",
             "", "  https://platform.claude.com/oauth/authorize?client_id=x&state=s", "  https://second.example/",
             '✓ Logged in as profile "default".']


def test_login(ant_proc, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    monkeypatch.setenv("ANTHROPIC_PROFILE", "other")
    monkeypatch.setenv("KEEP_ME", "1")
    ant_proc.proc = FakeProc(LOGIN_OUT, 0, on_exit=write_profile)
    opened: list[str] = []
    T.login("/x/ant.exe", opened.append, timeout=120)
    assert opened == ["https://platform.claude.com/oauth/authorize?client_id=x&state=s"]
    assert ant_proc.args == ["/x/ant.exe", "auth", "login", "--no-browser", "--callback-port", "0", "--timeout", "120s"]
    env = ant_proc.kw["env"]
    assert env["ANTHROPIC_CONFIG_DIR"] == str(T.signin_dir()) and env["KEEP_ME"] == "1"
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_PROFILE" not in env
    assert ant_proc.kw["creationflags"] == T.NO_WINDOW and ant_proc.kw["stdin"] == subprocess.DEVNULL
    assert T.signin_dir().is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_login_keeps_the_tokens_private(ant_proc):
    ant_proc.proc = FakeProc(LOGIN_OUT, 0, on_exit=write_profile)
    T.login("/x/ant", lambda url: None)
    d = T.signin_dir()
    assert os.stat(d).st_mode & 0o777 == 0o700
    assert os.stat(d / "credentials").st_mode & 0o777 == 0o700
    assert os.stat(d / "credentials" / "default.json").st_mode & 0o777 == 0o600
    assert os.stat(d / "configs" / "default.json").st_mode & 0o777 == 0o600


@pytest.mark.parametrize("lines, code, message", [
    (["Error: timed out waiting for authorization", ""], 1,
     r"didn't finish \(Error: timed out waiting for authorization\)\."),
    ([], 0, r"didn't finish\.$"),                                  # (ant said it worked, but no profile)
])
def test_login_that_didnt_finish(ant_proc, lines, code, message):
    ant_proc.proc = FakeProc(lines, code)
    with pytest.raises(T.AntError, match=message):
        T.login("/x/ant.exe", lambda url: None)


def test_login_cancelled_and_timed_out(ant_proc):
    cancel = threading.Event()
    cancel.set()
    ant_proc.proc = FakeProc([], 0, polls=100)
    with pytest.raises(T.AntError, match="Sign-in cancelled."):
        T.login("/x/ant.exe", lambda url: None, cancel)
    assert ant_proc.proc.killed
    ant_proc.proc = FakeProc([], 0, polls=100)
    with pytest.raises(T.AntError, match="Nobody signed in within 5 minutes"):
        T.login("/x/ant.exe", lambda url: None, timeout=-15)
    assert ant_proc.proc.killed


def test_logout(runs):
    write_profile()
    T.logout("/x/ant.exe")
    args, kw = runs.calls[0]
    assert args == ["/x/ant.exe", "auth", "logout"] and kw["env"]["ANTHROPIC_CONFIG_DIR"] == str(T.signin_dir())
    assert kw["creationflags"] == T.NO_WINDOW
    assert not T.signin_dir().exists()


def test_logout_without_the_helper_or_when_it_fails(runs):
    write_profile()
    T.logout(None)
    assert runs.calls == [] and not T.signin_dir().exists()
    write_profile()
    runs.answer = OSError("gone")
    T.logout("/x/ant.exe")
    assert not T.signin_dir().exists()
