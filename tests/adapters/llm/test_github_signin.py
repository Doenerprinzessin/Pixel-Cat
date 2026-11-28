"""github_signin: GitHub's OAuth device flow (start, polling, the user's login) against a fake
github.com on 127.0.0.1; waiting between polls is recorded, never slept."""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from adapters.llm.fakes import Json, Raw
from pixelcat.adapters.llm import github_signin as GS
from pixelcat.llm.external import MemoryCredentials

DEVICE = {"device_code": "DEV", "user_code": "ABCD-1234", "interval": 1, "expires_in": 60,
          "verification_uri": "https://github.com/login/device"}


@pytest.fixture
def github(fake_http, monkeypatch):
    monkeypatch.setattr(GS, "CLIENT_ID", "Iv-pixel")
    for name, path in (("device", "/login/device/code"), ("token", "/login/oauth/access_token"), ("user", "/user")):
        monkeypatch.setitem(GS.URLS, name, fake_http.url + path)
    fake_http.on("POST", "/login/device/code", Json(200, DEVICE))
    fake_http.on("GET", "/user", lambda req: Json(200, {"login": "octocat"})
                 if req.header("authorization") == "Bearer gho_fake" else Json(401, {"message": "Bad credentials"}))
    return fake_http


class Waits:
    """A cancel event that never waits: records each interval; ``cancel_at``: the wait that cancels."""

    def __init__(self, cancel_at: int | None = None) -> None:
        self.waited: list[float] = []
        self.cancel_at = cancel_at

    def wait(self, seconds: float) -> bool:
        self.waited.append(seconds)
        return self.cancel_at is not None and len(self.waited) >= self.cancel_at


def polls(github) -> list[dict]:
    return [r.form() for r in github.sent("/login/oauth/access_token")]


def test_without_a_client_id_it_is_hidden(monkeypatch):
    monkeypatch.setattr(GS, "CLIENT_ID", "")
    assert not GS.available()
    with pytest.raises(GS.SignInError, match="use a token instead"):
        GS.start()


def test_start(github):
    assert GS.available()
    assert GS.start() == DEVICE
    req = github.sent("/login/device/code")[0]
    assert req.form() == {"client_id": "Iv-pixel", "scope": "read:user"}
    assert req.header("accept") == "application/json" and req.header("user-agent") == "PixelCat"


@pytest.mark.parametrize("response, detail", [
    (Json(400, {"error": "bad_client", "error_description": "Unknown client"}), "Unknown client"),
    (Json(200, {"error": "device_flow_disabled"}), "device_flow_disabled"),
    (Raw(503, b"down"), "503"),
])
def test_start_refused(github, response, detail):
    github.on("POST", "/login/device/code", response)
    with pytest.raises(GS.SignInError, match=f"didn't start the sign-in \\({detail}\\)"):
        GS.start()


def test_start_offline(monkeypatch):
    monkeypatch.setattr(GS, "CLIENT_ID", "Iv-pixel")

    def post(*a: Any, **kw: Any) -> Any:
        raise GS.requests.ConnectionError("no route")
    monkeypatch.setattr(GS.requests, "post", post)
    with pytest.raises(GS.SignInError, match="couldn't reach GitHub"):
        GS.start()


def test_poll_until_approved(github):
    github.on("POST", "/login/oauth/access_token", Json(200, {"error": "authorization_pending"}),
              Json(200, {"error": "slow_down", "interval": 10}), Json(200, {"error": "slow_down"}),
              Json(200, {"access_token": "gho_fake", "token_type": "bearer"}))
    waits = Waits()
    assert GS.poll(dict(DEVICE, interval=0), waits) == "gho_fake"
    assert waits.waited == [5, 5, 10, 15]                 # (no interval: 5 s; slow_down: GitHub's or +5)
    assert polls(github)[0] == {"client_id": "Iv-pixel", "device_code": "DEV",
                                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}


@pytest.mark.parametrize("answer, message", [
    (Json(200, {"error": "access_denied"}), "declined on GitHub"),
    (Json(200, {"error": "expired_token"}), "ran out before it was entered"),
    (Json(200, {"error": "incorrect_device_code", "error_description": "Bad code"}), "refused the sign-in \\(Bad code\\)"),
    (Raw(500, b"oops"), "refused the sign-in \\(500\\)"),
])
def test_poll_refused(github, answer, message):
    github.on("POST", "/login/oauth/access_token", answer)
    with pytest.raises(GS.SignInError, match=message):
        GS.poll(DEVICE, Waits())
    assert len(polls(github)) == 1


def test_poll_cancelled(github):
    github.on("POST", "/login/oauth/access_token", Json(200, {"error": "authorization_pending"}))
    with pytest.raises(GS.SignInError, match="cancelled"):
        GS.poll(DEVICE, Waits(cancel_at=3))
    assert len(polls(github)) == 2


def test_poll_runs_out(monkeypatch):
    clock = iter([1000.0, 1000.0, 1100.0])
    monkeypatch.setattr(GS, "time", SimpleNamespace(time=lambda: next(clock), sleep=time.sleep))
    monkeypatch.setattr(GS.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(GS.requests.ConnectionError()))
    with pytest.raises(GS.SignInError, match="ran out"):
        GS.poll(dict(DEVICE, expires_in=60), Waits())


def test_poll_without_cancel_sleeps_the_interval(github, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(GS, "time", SimpleNamespace(time=time.time, sleep=slept.append))
    github.on("POST", "/login/oauth/access_token", Json(200, {"access_token": "gho_x"}))
    assert GS.poll(dict(DEVICE, interval=3)) == "gho_x"
    assert slept == [3]


def test_poll_network_blip_is_retried(github, monkeypatch):
    real = GS.requests.post
    calls: list[int] = []

    def post(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise GS.requests.ConnectionError("blip")
        return real(*a, **kw)
    monkeypatch.setattr(GS.requests, "post", post)
    github.on("POST", "/login/oauth/access_token", Json(200, {"access_token": "gho_after"}))
    assert GS.poll(DEVICE, Waits()) == "gho_after" and len(calls) == 2


def test_user(github):
    assert GS.user("gho_fake") == "octocat"
    assert GS.user("bad") == ""
    github.on("GET", "/user", Json(200, ["not a dict"]))
    assert GS.user("gho_fake") == ""


def test_user_offline(monkeypatch):
    def get(*a: Any, **kw: Any) -> Any:
        raise GS.requests.ConnectionError("down")
    monkeypatch.setattr(GS.requests, "get", get)
    assert GS.user("t") == ""


def test_sign_in_and_out(github, monkeypatch):
    github.on("POST", "/login/oauth/access_token", Json(200, {"error": "authorization_pending"}),
              Json(200, {"access_token": "gho_fake"}))
    creds = MemoryCredentials({})
    shown: list[tuple] = []
    monkeypatch.setattr(GS, "time", SimpleNamespace(time=lambda: 1_800_000_000.0, sleep=time.sleep))
    assert GS.sign_in(creds, lambda code, uri: shown.append((code, uri)), Waits()) == {"login": "octocat"}
    assert shown == [("ABCD-1234", "https://github.com/login/device")]
    assert creds.get("copilot_signin") == {"token": "gho_fake", "login": "octocat", "at": 1_800_000_000.0}
    assert GS.signed_in(creds)
    GS.sign_out(creds)
    assert not GS.signed_in(creds) and creds.get("copilot_signin") is None


def test_sign_in_default_verification_uri(github):
    github.on("POST", "/login/device/code", Json(200, {"device_code": "D", "interval": 1}))
    github.on("POST", "/login/oauth/access_token", Json(200, {"access_token": "gho_fake"}))
    shown: list[tuple] = []
    GS.sign_in(MemoryCredentials({}), lambda c, u: shown.append((c, u)), Waits())
    assert shown == [("", "https://github.com/login/device")]


@pytest.mark.parametrize("value, ok", [({"token": "t"}, True), ({"token": ""}, False), ("t", False), (None, False)])
def test_signed_in(value, ok):
    assert GS.signed_in(MemoryCredentials({"copilot_signin": value} if value is not None else {})) is ok
