"""copilot_client, pure parts: effort, quota pick, the history told to the runtime, errors,
tokens, the runtime's files and download, the SDK client's set-up, account calls (models,
login, quota) with a scripted SDK client."""
from __future__ import annotations

import asyncio
import io
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pixelcat import paths
from pixelcat.llm import copilot_client as CC
from pixelcat.llm.external import MemoryCredentials


# ------------------------------------------------------------------ effort and quota
@pytest.mark.parametrize("think, effort, efforts, expected", [
    (True, "medium", ["low", "medium", "high"], "medium"),
    (True, "max", ["low", "high"], "high"),
    (False, "high", ["low", "medium"], "low"),
    (False, "high", ["medium", "none"], "none"),
    (True, "low", ["medium", "high"], "medium"),
    (True, "high", [], None),
    (True, "high", ["turbo"], None),
    (True, "auto", ["low", "medium", "high"], "medium"),
    (True, "xhigh", ["minimal", "xhigh", "max"], "xhigh"),
])
def test_pick_effort(think, effort, efforts, expected):
    assert CC.pick_effort(think, effort, efforts) == expected


@pytest.mark.parametrize("snaps, key", [
    ({"chat": 1, "premium_interactions": 2, "ai_credits": 3}, "ai_credits"),
    ({"chat": 1, "AIC_monthly": 2}, "AIC_monthly"),
    ({"chat": 1, "premium_interactions": 2}, "premium_interactions"),
    ({"completions": 1, "chat": 2}, "chat"),
    ({"completions": 1, "other": 2}, "completions"),
])
def test_pick_quota(snaps, key):
    assert CC.pick_quota(snaps) == (key, snaps[key])


def test_pick_quota_nothing():
    assert CC.pick_quota({}) is None


# ------------------------------------------------------------------ the history told to the runtime
def test_compose_just_a_new_message():
    assert CC.compose([{"role": "user", "content": "hello"}]) == "hello"
    assert CC.compose([]) == "(continue)"


def test_compose_earlier_part_as_context():
    told = CC.compose([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Meow!"},
                       {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_time"}},
                                                                           {"function": {"name": "look"}}]},
                       {"role": "tool", "tool_name": "get_time", "content": "12:00"},
                       {"role": "user", "content": "and now?"}, {"role": "user", "content": "say it",
                                                                 "images": ["x"]}])
    assert told == ("(Earlier in this chat - for context, already done:)\nUser: hi\nYou: Meow!\nYou:  [used: get_time, "
                    "look]\n[result of get_time] 12:00\n\n(Now:)\nand now?\n\nsay it (a picture was attached - I can't "
                    "see it here)")


def test_compose_only_results_have_no_now_header():
    told = CC.compose([{"role": "tool", "tool_name": "look", "content": ""},
                       {"role": "user", "_tool": True, "content": "[tool result for look] seen it"},
                       {"role": "user", "content": "ok"}])
    assert told == "[result of look] (done)\n[result of a tool] seen it\n\nok"


def test_compose_without_a_new_message():
    assert CC.compose([{"role": "user", "content": "a"}, {"role": "tool", "content": "r"}]) == (
        "(Earlier in this chat - for context, already done:)\nUser: a\n[result of a tool] r\n\n(Now:)\n(continue)")


def test_compose_clips_and_drops_the_oldest():
    long = "word " * 400
    msgs = [{"role": "user", "content": f"{i} {long}"} for i in range(20)] + [{"role": "assistant", "content": "x"},
                                                                                {"role": "user", "content": "now"}]
    told = CC.compose(msgs)
    lines = told.split("\n")
    assert all(len(ln) <= 1200 + len("User: ") + 2 for ln in lines)
    assert "User: 0 " not in told and "User: 19 " in told
    assert sum(len(ln) for ln in lines[1:-3]) <= 14_000
    assert told.endswith("(Now:)\nnow")


# ------------------------------------------------------------------ errors and tokens
@pytest.mark.parametrize("message, kind", [
    ("HTTP 401 Unauthorized", "auth"), ("Bad credentials", "auth"), ("token is invalid", "auth"),
    ("403 Forbidden", "denied"), ("Copilot access not enabled", "denied"), ("user is not entitled", "denied"),
    ("getaddrinfo failed", "ext_offline"), ("could not connect", "ext_offline"), ("request timed out", "ext_offline"),
    ("something else", "error"),
])
def test_error_of(message, kind):
    err = CC._error_of(RuntimeError(message))
    assert (err.kind, str(err)) == (kind, message)


def test_error_of_keeps_copilot_errors():
    e = CC.CopilotError("install", "x")
    assert CC._error_of(e) is e


@pytest.mark.parametrize("data, auth, token", [
    ({"copilot_key": "github_pat_1"}, "key", "github_pat_1"),
    ({"copilot_signin": {"token": "gho_1"}, "copilot_key": "k"}, "signin", "gho_1"),
])
def test_token_of(data, auth, token):
    assert CC.token_of(MemoryCredentials(data), auth) == token


@pytest.mark.parametrize("data, auth", [({}, "key"), ({"copilot_key": "k"}, "signin"),
                                        ({"copilot_signin": "junk"}, "signin")])
def test_token_of_missing(data, auth):
    with pytest.raises(CC.CopilotError) as e:
        CC.token_of(MemoryCredentials(data), auth)
    assert e.value.kind == "auth"


def test_token_of_with_byok(monkeypatch):
    monkeypatch.setattr(CC, "BYOK", {"provider": {"name": "fake"}})
    assert CC.token_of(MemoryCredentials({}), "key") == ""


# ------------------------------------------------------------------ files
def test_folders():
    local = Path(os.environ["LOCALAPPDATA"])
    assert CC.tools_dir() == local / "PixelCat" / "tools" / "copilot"
    assert CC.home_dir() == paths.data_dir() / "copilot" and CC.home_dir().is_dir()
    assert CC._work_dir() == CC.home_dir() / "workspace" and CC._work_dir().is_dir()


def test_prune_logs():
    logs = CC.home_dir() / "logs"
    logs.mkdir()
    old, new, other = logs / "process-1.log", logs / "process-2.log", logs / "keep.txt"
    for f in (old, new, other):
        f.write_text("x")
    t = time.time() - 20 * 86400
    os.utime(old, (t, t))
    os.utime(other, (t, t))
    CC._prune_logs()
    assert not old.exists() and new.exists() and other.exists()


# ------------------------------------------------------------------ the runtime's download
class FakeDL:
    def __init__(self, tmp: Path, complete: bool | None = True) -> None:
        self.tmp = tmp
        self.complete = complete
        self.fetched: list[bytes] = []
        if complete is not None:
            self._runtime_bundle_is_complete = lambda pair, name: self.complete

    def get_cache_dir(self, version: str | None = None) -> Path:
        return self.tmp / "cache" / (version or "")

    def _fetch_url_bytes(self, url: str, *, timeout: int) -> bytes:
        raise AssertionError("the original fetch must not be used")

    def ensure_runtime_wrapper(self) -> str:
        self.fetched.append(self._fetch_url_bytes("https://github.com/github/copilot-cli/x.tgz", timeout=7))
        return str(self.tmp / "copilot-runtime.exe")


@pytest.fixture
def fake_dl(tmp_path, monkeypatch):
    dl = FakeDL(tmp_path)
    monkeypatch.setattr(CC, "_dl", lambda: dl)
    return dl


def test_runtime_path(fake_dl):
    from copilot._cli_version import CLI_VERSION, get_runtime_platform

    assert CC.runtime_path() == str(fake_dl.get_cache_dir(CLI_VERSION) / "prebuilds" / get_runtime_platform()
                                    / "copilot-runtime.exe")
    fake_dl.complete = False
    assert CC.runtime_path() is None


def test_runtime_path_without_the_sdks_check(tmp_path, monkeypatch):
    from copilot._cli_version import CLI_VERSION, get_runtime_platform

    dl = FakeDL(tmp_path, complete=None)
    monkeypatch.setattr(CC, "_dl", lambda: dl)
    assert CC.runtime_path() is None
    pair = dl.get_cache_dir(CLI_VERSION) / "prebuilds" / get_runtime_platform()
    pair.mkdir(parents=True)
    (pair / "copilot-runtime.exe").write_bytes(b"x")
    assert CC.runtime_path() is None
    (pair / "runtime.node").write_bytes(b"x")
    assert CC.runtime_path() == str(pair / "copilot-runtime.exe")


def test_runtime_path_when_the_sdk_fails(monkeypatch):
    def broken():
        raise ImportError("no SDK")
    monkeypatch.setattr(CC, "_dl", broken)
    assert CC.runtime_path() is None


def test_dl_points_the_sdk_at_pixel_cats_tools_folder(monkeypatch):
    from copilot import _cli_download as dl

    monkeypatch.setattr(dl, "get_cache_dir", dl.get_cache_dir)
    monkeypatch.setattr(dl, "_pixelcat", False, raising=False)
    assert CC._dl() is dl
    assert dl.get_cache_dir("1.0.117") == CC.tools_dir() / dl._sanitize_version("1.0.117")
    assert dl.get_cache_dir() == CC.tools_dir()
    patched = dl.get_cache_dir
    CC._dl()
    assert dl.get_cache_dir is patched


class FakeURL:
    def __init__(self, data: bytes, length: int | None = None) -> None:
        self.buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self) -> "FakeURL":
        return self

    def __exit__(self, *a: Any) -> None:
        pass

    def read(self, n: int) -> bytes:
        return self.buf.read(n)


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(CC, "time", SimpleNamespace(time=time.time, sleep=slept.append))
    return slept


def test_install_downloads_with_progress(fake_dl, monkeypatch):
    data = os.urandom(1_200_000)
    seen: list[Any] = []

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        return FakeURL(data)
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    steps: list[tuple[int, int]] = []
    path = CC.install(lambda d, t: steps.append((d, t)))
    assert path == str(fake_dl.tmp / "copilot-runtime.exe")
    assert fake_dl.fetched == [data]
    assert seen == [("https://github.com/github/copilot-cli/x.tgz", "PixelCat", 7)]
    assert steps[-1] == (len(data), len(data)) and len(steps) == 5
    assert fake_dl._fetch_url_bytes.__name__ == "_fetch_url_bytes"        # (the SDK's own put back)


def test_install_when_already_there(monkeypatch):
    monkeypatch.setattr(CC, "runtime_path", lambda: "/have/it")
    assert CC.install() == "/have/it"


def test_install_small_download_no_progress(fake_dl, monkeypatch):
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeURL(b"tiny"))
    steps: list = []
    CC.install(lambda d, t: steps.append(d))
    assert steps == [] and fake_dl.fetched == [b"tiny"]


def test_install_cancelled(fake_dl, monkeypatch):
    import threading

    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeURL(b"x" * 10))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CC.CopilotError, match="Cancelled") as e:
        CC.install(cancel=cancel)
    assert e.value.kind == "install"


def test_install_too_big(fake_dl, monkeypatch):
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(CC, "MAX_DOWNLOAD", 300_000)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeURL(b"x" * 600_000))
    with pytest.raises(CC.CopilotError, match="far too big"):
        CC.install()


def test_install_retries_then_offline(fake_dl, monkeypatch, no_sleep):
    calls: list[int] = []

    def urlopen(req, timeout):
        calls.append(1)
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(CC.CopilotError, match="couldn't reach GitHub") as e:
        CC.install()
    assert e.value.kind == "ext_offline" and len(calls) == 3 and no_sleep == [1, 2]


def test_install_retry_succeeds(fake_dl, monkeypatch, no_sleep):
    answers: list[Any] = [TimeoutError("slow"), FakeURL(b"ok")]

    def urlopen(req, timeout):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    CC.install()
    assert fake_dl.fetched == [b"ok"] and no_sleep == [1]


def test_install_sdk_failure_is_an_install_error(fake_dl, monkeypatch):
    def broken() -> str:
        raise ValueError("checksum mismatch")
    fake_dl.ensure_runtime_wrapper = broken
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    with pytest.raises(CC.CopilotError, match="checksum mismatch") as e:
        CC.install()
    assert e.value.kind == "install"


# ------------------------------------------------------------------ the SDK client
def test_new_client_is_isolated(monkeypatch):
    import copilot

    made: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kw: Any) -> None:
            made["client"] = kw

    def spawned(**kw: Any) -> str:
        made["runtime"] = kw
        return "RUNTIME"
    monkeypatch.setattr(copilot, "CopilotClient", FakeClient)
    monkeypatch.setattr(copilot.RuntimeConnection, "for_spawned_stdio", staticmethod(spawned))
    monkeypatch.setenv("GH_TOKEN", "leak")
    monkeypatch.setenv("COPILOT_HOME", "leak")
    monkeypatch.setenv("PIXELCAT_KEEP", "yes")
    CC.new_client("gho_1", "/rt/copilot-runtime.exe")
    assert made["client"] == {"mode": "empty", "github": {"use_logged_in_user": False, "token": "gho_1"},
                              "runtime": "RUNTIME"}
    env = made["runtime"]["env"]
    assert "GH_TOKEN" not in env and "COPILOT_HOME" not in env and env["PIXELCAT_KEEP"] == "yes"
    assert env["COPILOT_TELEMETRY_DISABLED"] == "true"
    assert made["runtime"]["path"] == "/rt/copilot-runtime.exe"
    assert made["runtime"]["base_directory"] == str(CC.home_dir())
    assert made["runtime"]["working_directory"] == str(CC._work_dir())
    CC.new_client("", "/rt")
    assert made["client"]["github"] == {"use_logged_in_user": False}


def run_async(coro: Any, timeout: float = 5) -> Any:
    return CC._LOOP.run(coro, timeout)


class StartClient:
    def __init__(self, fail_start: bool = False, fail_stop: bool = False) -> None:
        self.fail_start, self.fail_stop = fail_start, fail_stop
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")
        if self.fail_start:
            raise OSError("spawn failed")

    async def stop(self) -> None:
        self.calls.append("stop")
        if self.fail_stop:
            raise RuntimeError("stuck")

    async def force_stop(self) -> None:
        self.calls.append("force_stop")

    async def list_sessions(self) -> list:
        self.calls.append("list_sessions")
        return [SimpleNamespace(session_id="old1"), SimpleNamespace(session_id="old2")]

    async def delete_session(self, sid: str) -> None:
        self.calls.append(f"delete:{sid}")


def test_make_client(monkeypatch):
    c = StartClient()
    monkeypatch.setattr(CC, "runtime_path", lambda: "/rt")
    monkeypatch.setattr(CC, "new_client", lambda token, path: c)
    assert run_async(CC._make_client("t")) is c and c.calls == ["start"]


def test_make_client_failures(monkeypatch):
    monkeypatch.setattr(CC, "runtime_path", lambda: None)
    with pytest.raises(CC.CopilotError) as e:
        run_async(CC._make_client("t"))
    assert e.value.kind == "install"
    monkeypatch.setattr(CC, "runtime_path", lambda: "/rt")
    monkeypatch.setattr(CC, "new_client", lambda token, path: StartClient(fail_start=True))
    with pytest.raises(CC.CopilotError, match="didn't start") as e:
        run_async(CC._make_client("t"))
    assert e.value.kind == "error"


def test_stop_client_forces_a_stuck_runtime():
    c = StartClient(fail_stop=True)
    run_async(CC._stop_client(c))
    assert c.calls == ["stop", "force_stop"]


def test_runtime_one_client_per_token(monkeypatch):
    made: list[StartClient] = []

    async def make(token: str) -> StartClient:
        made.append(StartClient())
        return made[-1]
    monkeypatch.setattr(CC, "_make_client", make)
    rt = CC._Runtime()
    a = run_async(rt.get("t1"))
    assert run_async(rt.get("t1")) is a and len(made) == 1
    assert a.calls == ["list_sessions", "delete:old1", "delete:old2"]
    b = run_async(rt.get("t2"))
    assert b is not a and a.calls[-1] == "stop" and (rt.client, rt.token) == (b, "t2")
    run_async(rt.stop())
    assert rt.client is None and b.calls[-1] == "stop"


def test_shutdown(monkeypatch):
    stopped: list[int] = []

    class RT:
        client = object()

        async def stop(self) -> None:
            stopped.append(1)
    monkeypatch.setattr(CC, "_RT", RT())
    CC.shutdown()
    assert stopped == [1]
    RT.client = None
    CC.shutdown()
    assert stopped == [1]


# ------------------------------------------------------------------ account calls
def model(mid: str, name: str | None = None, state: str | None = None, sup: Any = "default",
          efforts: list | None = None) -> Any:
    if sup == "default":
        sup = SimpleNamespace(tool_calls=True, reasoning_effort=True, vision=True)
    return SimpleNamespace(id=mid, name=name, policy=SimpleNamespace(state=SimpleNamespace(value=state)) if state
                           else None, capabilities=SimpleNamespace(supports=sup) if sup is not None else None,
                           supported_reasoning_efforts=efforts)


class AccountClient:
    def __init__(self, models: list | Exception = (), auth: Any = None, quota: Any = None) -> None:
        self.models, self.auth, self.quota_res = models, auth, quota
        self.rpc = SimpleNamespace(account=SimpleNamespace(get_quota=self.get_quota))

    async def list_models(self) -> list:
        if isinstance(self.models, Exception):
            raise self.models
        return list(self.models)

    async def get_auth_status(self) -> Any:
        if isinstance(self.auth, Exception):
            raise self.auth
        return self.auth

    async def get_quota(self, req: Any) -> Any:
        if isinstance(self.quota_res, Exception):
            raise self.quota_res
        return self.quota_res


class FakeRT:
    def __init__(self, client: Any, token: str | None = None) -> None:
        self.client, self.token = client, token
        self.asked: list[str] = []

    async def get(self, token: str) -> Any:
        self.asked.append(token)
        self.token = token
        return self.client


MODELS = [
    model("gpt-x", "GPT X", efforts=["low", "high"]),
    model("off", state="disabled"),
    model("enabled", "Enabled", state="enabled", efforts=None),
    model("no-tools", sup=SimpleNamespace(tool_calls=False, reasoning_effort=True, vision=True)),
    model("no-effort", sup=SimpleNamespace(tool_calls=True, reasoning_effort=False, vision=False), efforts=["low"]),
    model("bare", sup=None, efforts=["medium"]),
]
LISTED = [{"id": "gpt-x", "name": "GPT X", "efforts": ["low", "high"], "vision": True},
          {"id": "enabled", "name": "Enabled", "efforts": [], "vision": True},
          {"id": "no-effort", "name": "no-effort", "efforts": [], "vision": False},
          {"id": "bare", "name": "bare", "efforts": ["medium"], "vision": False}]


def test_list_models(monkeypatch):
    rt = FakeRT(AccountClient(MODELS))
    monkeypatch.setattr(CC, "_RT", rt)
    assert CC.list_models(MemoryCredentials({"copilot_key": "github_pat_1"}), "key") == LISTED
    assert rt.asked == ["github_pat_1"]


def test_list_models_errors_are_mapped(monkeypatch):
    monkeypatch.setattr(CC, "_RT", FakeRT(AccountClient(RuntimeError("401 Unauthorized"))))
    with pytest.raises(CC.CopilotError) as e:
        CC.list_models(MemoryCredentials({"copilot_key": "k"}), "key")
    assert e.value.kind == "auth"


def test_check_token(monkeypatch):
    stopped: list[Any] = []
    client = AccountClient(MODELS, SimpleNamespace(is_authenticated=True, login="octocat", status_message=""))

    async def make(token: str) -> Any:
        assert token == "github_pat_try"
        return client

    async def stop(c: Any) -> None:
        stopped.append(c)
    monkeypatch.setattr(CC, "_make_client", make)
    monkeypatch.setattr(CC, "_stop_client", stop)
    assert CC.check_token("github_pat_try") == ("octocat", LISTED)
    assert stopped == [client]


@pytest.mark.parametrize("auth, models, kind, message", [
    (SimpleNamespace(is_authenticated=False, login=None, status_message="Token expired"), MODELS, "auth",
     "Token expired"),
    (SimpleNamespace(is_authenticated=False, login=None, status_message=""), MODELS, "auth",
     "GitHub didn't accept that token."),
    (RuntimeError("network unreachable"), MODELS, "ext_offline", "network unreachable"),
    (SimpleNamespace(is_authenticated=True, login=None, status_message=""), [], "denied",
     "That token can't use Copilot models."),
])
def test_check_token_refused(monkeypatch, auth, models, kind, message):
    stopped: list[Any] = []

    async def make(token: str) -> Any:
        return AccountClient(models, auth)

    async def stop(c: Any) -> None:
        stopped.append(c)
    monkeypatch.setattr(CC, "_make_client", make)
    monkeypatch.setattr(CC, "_stop_client", stop)
    with pytest.raises(CC.CopilotError) as e:
        CC.check_token("t")
    assert (e.value.kind, str(e.value)) == (kind, message)
    assert len(stopped) == 1                                     # (the trial runtime always stops)


def snap(**kw: Any) -> Any:
    base = dict(entitlement_requests=20000, used_requests=3000, remaining_percentage=84.4,
                is_unlimited_entitlement=False, reset_date="2026-12-01T00:00:00Z",
                overage_allowed_with_exhausted_quota=False)
    return SimpleNamespace(**{**base, **kw})


def test_quota(monkeypatch):
    res = SimpleNamespace(quota_snapshots={"chat": snap(entitlement_requests=0), "ai_credits": snap()})
    monkeypatch.setattr(CC, "_RT", FakeRT(AccountClient(quota=res)))
    q = CC.quota(MemoryCredentials({"copilot_key": "k"}), "key", start=True)
    at = q.pop("at")
    assert abs(at - time.time()) < 5
    assert q == {"kind": "ai_credits", "used": 3120.0, "total": 20000.0, "unlimited": False,
                 "reset": "2026-12-01T00:00:00Z", "overage_ok": False}


def test_quota_without_a_percentage_or_entitlement(monkeypatch):
    res = SimpleNamespace(quota_snapshots={"premium": snap(remaining_percentage=None, used_requests=12.34,
                                                           reset_date=None, is_unlimited_entitlement=True,
                                                           overage_allowed_with_exhausted_quota=True)})
    monkeypatch.setattr(CC, "_RT", FakeRT(AccountClient(quota=res)))
    q = CC.quota(MemoryCredentials({"copilot_key": "k"}), "key", start=True)
    assert (q["used"], q["reset"], q["unlimited"], q["overage_ok"]) == (12.3, "", True, True)


def test_quota_only_while_the_runtime_runs(monkeypatch):
    rt = FakeRT(None)
    monkeypatch.setattr(CC, "_RT", rt)
    assert CC.quota(MemoryCredentials({"copilot_key": "k"}), "key") is None
    rt.client, rt.token = AccountClient(quota=SimpleNamespace(quota_snapshots={})), "other"
    assert CC.quota(MemoryCredentials({"copilot_key": "k"}), "key") is None and rt.asked == []
    rt.token = "k"
    assert CC.quota(MemoryCredentials({"copilot_key": "k"}), "key") is None and rt.asked == ["k"]


@pytest.mark.parametrize("creds, client", [
    ({}, AccountClient()),
    ({"copilot_key": "k"}, AccountClient(quota=RuntimeError("rpc failed"))),
])
def test_quota_failures_are_none(monkeypatch, creds, client):
    monkeypatch.setattr(CC, "_RT", FakeRT(client))
    assert CC.quota(MemoryCredentials(creds), "key", start=True) is None


def test_bring_your_own_key_mode(monkeypatch):
    byok = {"provider": {"name": "fake"}, "models": [{"id": "m1", "vision": True}], "login": "octocat",
            "quota": {"kind": "ai_credits", "used": 1.0}}
    monkeypatch.setattr(CC, "BYOK", byok)
    monkeypatch.setattr(CC, "_RT", FakeRT(None))
    assert CC.list_models(MemoryCredentials({}), "key") == [{"id": "m1", "vision": True}]
    assert CC.check_token("") == ("octocat", [{"id": "m1", "vision": True}])
    q = CC.quota(MemoryCredentials({}), "key")
    assert q["kind"] == "ai_credits" and "at" in q
    byok["models"] = []
    with pytest.raises(CC.CopilotError):
        CC.check_token("")
    byok["quota"] = None
    assert CC.quota(MemoryCredentials({}), "key") is None


def test_loop_runs_coroutines_on_its_own_thread():
    import threading

    async def where() -> str:
        await asyncio.sleep(0)
        return threading.current_thread().name
    assert run_async(where()) == "pixelcat-copilot"
    assert CC._LOOP.get() is CC._LOOP.get()
