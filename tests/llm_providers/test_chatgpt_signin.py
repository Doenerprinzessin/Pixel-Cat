"""chatgpt_signin: "Sign in with ChatGPT" (OAuth code + PKCE with a loopback listener, the ID
token's checks, renewing and revoking) against a fake auth.openai.com on 127.0.0.1."""
from __future__ import annotations

import base64
import hashlib
import json
import random
import threading
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import requests

from llm_providers.fakes import Json
from pixelcat.llm import chatgpt_signin as CS
from pixelcat.llm.external import MemoryCredentials

FULL_SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


# ------------------------------------------------------------------ a throwaway RSA key (signs ID tokens)
def _is_prime(n: int, rnd: random.Random) -> bool:
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(16):
        x = pow(rnd.randrange(2, n - 2), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, rnd: random.Random) -> int:
    while True:
        c = rnd.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(c, rnd):
            return c


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _key() -> tuple[int, int]:
    rnd = random.Random(20261127)
    e = 65537
    while True:
        p, q = _prime(512, rnd), _prime(512, rnd)
        if ((p - 1) * (q - 1)) % e:
            return p * q, pow(e, -1, (p - 1) * (q - 1))


N, D = _key()
JWK = {"kty": "RSA", "kid": "k1", "n": b64u(N.to_bytes((N.bit_length() + 7) // 8, "big")), "e": b64u((65537).to_bytes(3, "big"))}


def jwt(claims: dict, bad: bool = False, alg: str = "RS256", kid: str = "k1") -> str:
    h = b64u(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    b = b64u(json.dumps(claims).encode())
    size = (N.bit_length() + 7) // 8
    info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(f"{h}.{b}".encode()).digest()
    em = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
    sig = pow(int.from_bytes(em, "big"), D, N) + (1 if bad else 0)
    return f"{h}.{b}.{b64u(sig.to_bytes(size, 'big'))}"


# ------------------------------------------------------------------ the fake auth.openai.com
class Auth:
    def __init__(self, http) -> None:
        self.http = http
        self.base = http.url + "/auth"
        self.refuse_codes = 0
        self.bad_sig = False
        self.claims: dict[str, Any] = {}
        self.scope = FULL_SCOPE
        self.revoke_status = 200
        self.callback: dict[str, str] = {}          # extra/changed fields the "browser" brings back
        self.pages: list[tuple[int, str]] = []
        self.opened: list[dict[str, str]] = []
        http.on("GET", "/auth/.well-known/openid-configuration", Json(200, {
            "issuer": self.base, "jwks_uri": self.base + "/jwks", "revocation_endpoint": self.base + "/revoke"}))
        http.on("GET", "/auth/jwks", Json(200, {"keys": [JWK]}))
        http.on("POST", "/auth/token", self.token)
        http.on("POST", "/auth/revoke", lambda req: Json(self.revoke_status, {}))

    def token(self, req) -> Json:
        form = req.form()
        if form["grant_type"] == "refresh_token":
            return Json(200, {"access_token": "AT-new", "refresh_token": "R2", "expires_in": 3600})
        if self.refuse_codes > 0:
            self.refuse_codes -= 1
            return Json(400, {"error": "invalid_grant", "error_description": "Invalid authorization code"})
        nonce = self.opened[-1]["nonce"]
        claims = {"iss": self.base, "aud": form["client_id"], "exp": time.time() + 3600, "nonce": nonce,
                  "email": "cat@example.com", "sub": "u1", **self.claims}
        return Json(200, {"access_token": "AT1", "refresh_token": "R1", "id_token": jwt(claims, self.bad_sig),
                          "expires_in": 1800, "token_type": "Bearer", "scope": self.scope,
                          "earliest_refresh_at": 5})

    def browser(self, url: str) -> None:
        """The user's browser: OpenAI approves and redirects back to the app's listener."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.opened.append(q)
        back = {"code": "CODE1", "state": q["state"], "scope": q["scope"]}
        if q["client_id"] == "dynamic_agent_client":
            back["client_id"] = "oaiapp_123"
        back.update(self.callback)
        back = {k: v for k, v in back.items() if v is not None}

        def go() -> None:
            r = requests.get(q["redirect_uri"] + "?" + urlencode(back), timeout=10)
            self.pages.append((r.status_code, r.text))
        threading.Thread(target=go, daemon=True).start()

    def page(self) -> tuple[int, str]:
        end = time.monotonic() + 5
        while not self.pages and time.monotonic() < end:
            time.sleep(0.01)
        return self.pages[0]


@pytest.fixture
def auth(fake_http, monkeypatch):
    a = Auth(fake_http)
    monkeypatch.setitem(CS.URLS, "issuer", a.base)
    monkeypatch.setitem(CS.URLS, "authorize", a.base + "/authorize")
    monkeypatch.setitem(CS.URLS, "token", a.base + "/token")
    monkeypatch.setitem(CS.URLS, "discovery", a.base + "/.well-known/openid-configuration")
    monkeypatch.setattr(CS, "PORT", 0)
    return a


@pytest.fixture
def creds():
    return MemoryCredentials({})


def tokens(auth) -> list[dict[str, str]]:
    return [r.form() for r in auth.http.sent("/auth/token")]


# ------------------------------------------------------------------ signing in
def test_first_sign_in(auth, creds, monkeypatch):
    monkeypatch.setattr(CS, "time", SimpleNamespace(time=lambda: 1_800_000_000.0, monotonic=time.monotonic,
                                                    sleep=time.sleep))
    auth.claims = {"exp": 1_800_003_600}
    info = CS.sign_in(creds, open_url=auth.browser, timeout=10)
    q = auth.opened[0]
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "Pixel Cat"
    assert q["response_type"] == "code" and q["scope"] == CS.SCOPES and q["resource"] == "https://api.openai.com/v1"
    assert q["code_challenge_method"] == "S256" and q["ext_agent_host_id"].startswith("urn:uuid:")
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
    assert "id_token_hint" not in q and len(q["state"]) >= 20 and len(q["nonce"]) >= 20
    tok = tokens(auth)[0]
    assert tok["grant_type"] == "authorization_code" and tok["client_id"] == "oaiapp_123"
    assert tok["code"] == "CODE1" and tok["redirect_uri"] == q["redirect_uri"]
    assert b64u(hashlib.sha256(tok["code_verifier"].encode()).digest()) == q["code_challenge"]
    assert info == {"client_id": "oaiapp_123", "access_token": "AT1", "refresh_token": "R1",
                    "id_token": info["id_token"], "expires_at": 1_800_001_800.0, "earliest_refresh_at": 5.0,
                    "email": "cat@example.com", "plan": True, "validated": True}
    assert creds.get("chatgpt") == info and CS.signed_in(creds)
    status, page = auth.page()
    assert status == 200 and "Pixel Cat is signed in with ChatGPT." in page


def test_next_sign_in_reuses_the_registration(auth, creds):
    first_id = CS.sign_in(creds, open_url=auth.browser, timeout=10)["id_token"]
    first = auth.opened[0]
    CS.sign_in(creds, open_url=auth.browser, timeout=10)
    q = auth.opened[1]
    assert q["client_id"] == "oaiapp_123" and "agent_name_hint" not in q
    assert q["id_token_hint"] == first_id
    assert q["ext_agent_host_id"] == first["ext_agent_host_id"]
    assert creds.get("chatgpt_host") == first["ext_agent_host_id"]


def test_without_the_plan_scope(auth, creds):
    auth.scope = "openid profile email offline_access"
    assert CS.sign_in(creds, open_url=auth.browser, timeout=10)["plan"] is False


def test_a_bad_signature_is_refused(auth, creds):
    auth.bad_sig = True
    with pytest.raises(CS.SignInError, match="bad signature"):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)
    assert not CS.signed_in(creds)


@pytest.mark.parametrize("claims", [{"iss": "https://evil.example"}, {"aud": "someone-else"},
                                    {"aud": ["x", "y"]}, {"exp": 1000}, {"nonce": "other"}])
def test_id_token_claims_are_checked(auth, creds, claims):
    auth.claims = claims
    with pytest.raises(CS.SignInError, match="didn't check out, so I didn't use it"):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)


def test_id_token_audience_list_is_accepted(auth, creds):
    auth.claims = {"aud": ["other", "oaiapp_123"]}
    assert CS.sign_in(creds, open_url=auth.browser, timeout=10)["validated"]


def test_refused_code_is_asked_again_with_the_issued_client(auth, creds):
    auth.refuse_codes = 1
    again: list[int] = []
    info = CS.sign_in(creds, open_url=auth.browser, timeout=10, again=lambda: again.append(1))
    assert [q["client_id"] for q in auth.opened] == ["dynamic_agent_client", "oaiapp_123"]
    assert "agent_name_hint" not in auth.opened[1] and again == [1]
    assert [t["client_id"] for t in tokens(auth)] == ["oaiapp_123", "oaiapp_123"]
    assert info["plan"] and CS.signed_in(creds)


def test_refused_twice_shows_openais_reason_and_keeps_nothing(auth, creds):
    auth.refuse_codes = 2
    with pytest.raises(CS.SignInError, match=r"didn't accept the sign-in \(Invalid authorization code\)"):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)
    assert not creds.get("chatgpt")


def test_a_registration_that_never_worked_is_dropped(auth, creds):
    creds.set("chatgpt", {"client_id": "oaiapp_stale"})
    CS.sign_in(creds, open_url=auth.browser, timeout=10)
    assert auth.opened[0]["client_id"] == "dynamic_agent_client"
    assert creds.get("chatgpt")["client_id"] == "oaiapp_123"


@pytest.mark.parametrize("callback, message", [
    ({"state": "forged"}, "didn't match"),
    ({"error": "access_denied", "code": None}, "You said no to the sign-in."),
    ({"error": "server_error", "code": None}, r"didn't work \(server_error\)"),
    ({"client_id": None}, "didn't match"),
])
def test_callback_problems(auth, creds, callback, message):
    auth.callback = callback
    with pytest.raises(CS.SignInError, match=message):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)
    assert tokens(auth) == []


def test_issued_client_must_match_the_registered_one(auth, creds):
    creds.set("chatgpt", {"client_id": "oaiapp_123", "validated": True})
    auth.callback = {"client_id": "oaiapp_other"}
    with pytest.raises(CS.SignInError, match="didn't match"):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)


def test_callback_page_without_a_code(auth, creds):
    auth.callback = {"error": "access_denied", "code": None}
    with pytest.raises(CS.SignInError):
        CS.sign_in(creds, open_url=auth.browser, timeout=10)
    status, page = auth.page()
    assert status == 200 and "Pixel Cat isn't signed in." in page


def test_cancel_and_timeout(auth, creds):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CS.SignInError, match="cancelled"):
        CS.sign_in(creds, open_url=lambda url: None, cancel=cancel, timeout=10)
    with pytest.raises(CS.SignInError, match="Nobody signed in"):
        CS.sign_in(creds, open_url=lambda url: None, timeout=0)


def test_listener_answers_other_paths_with_404_and_uses_another_port_when_busy(auth, creds, monkeypatch):
    import socket

    busy = socket.socket()
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # (Windows: or the listener could share the port)
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    monkeypatch.setattr(CS, "PORT", busy.getsockname()[1])
    seen: list[int] = []

    def browser(url: str) -> None:
        redirect = parse_qs(urlparse(url).query)["redirect_uri"][0]
        seen.append(int(urlparse(redirect).port))

        def first_a_wrong_page() -> None:
            seen.append(requests.get(redirect.replace("/auth/callback", "/favicon.ico"), timeout=5).status_code)
            auth.browser(url)
        threading.Thread(target=first_a_wrong_page, daemon=True).start()
    try:
        CS.sign_in(creds, open_url=browser, timeout=10)
    finally:
        busy.close()
    assert seen[0] != CS.PORT and seen[1] == 404


def test_id_token_check_offline(auth, monkeypatch):
    def get(*a, **kw):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(CS.requests, "get", get)
    with pytest.raises(CS.SignInError, match="Couldn't check the sign-in"):
        CS.check_id_token(jwt({}), "c", None)


def test_id_token_check(auth):
    claims = {"iss": auth.base, "aud": "c", "exp": time.time() + 60, "nonce": "n", "email": "e"}
    assert CS.check_id_token(jwt(claims), "c", "n") == claims
    assert CS.check_id_token(jwt(claims), "c", None) == claims
    with pytest.raises(CS.SignInError, match="Couldn't check"):
        CS.check_id_token("not-a-jwt", "c", None)
    with pytest.raises(CS.SignInError, match="bad signature"):
        CS.check_id_token(jwt(claims, kid="other"), "c", "n")          # (no key with that id)


def test_id_token_that_isnt_rs256_skips_the_signature_check(auth):
    claims = {"iss": auth.base, "aud": "c", "exp": time.time() + 60}
    token = jwt(claims, bad=True, alg="HS256")
    assert CS.check_id_token(token, "c", None) == claims         # current behaviour: accepted


# ------------------------------------------------------------------ registrations
@pytest.mark.parametrize("saved, cid", [
    ({}, None), ({"client_id": "c"}, None), ({"client_id": "c", "validated": True}, "c"),
    ({"client_id": "c", "refresh_token": "r"}, "c"), ({"client_id": "c", "access_token": "a"}, "c"),
    ({"validated": True}, None),
])
def test_registered(saved, cid):
    assert CS.registered(saved) == cid


def test_forget_unconfirmed():
    c = MemoryCredentials({"chatgpt": {"client_id": "x"}})
    CS.forget_unconfirmed(c)
    assert c.get("chatgpt") is None
    c = MemoryCredentials({"chatgpt": {"client_id": "x", "validated": True}})
    CS.forget_unconfirmed(c)
    assert c.get("chatgpt") == {"client_id": "x", "validated": True}


def test_host_id_is_made_once():
    c = MemoryCredentials({})
    hid = CS.host_id(c)
    assert hid.startswith("urn:uuid:") and CS.host_id(c) == hid


# ------------------------------------------------------------------ access tokens
def signed(**kw: Any) -> MemoryCredentials:
    base = {"client_id": "oaiapp_123", "access_token": "AT1", "refresh_token": "R1", "id_token": "old",
            "expires_at": time.time() + 3600, "earliest_refresh_at": 0}
    return MemoryCredentials({"chatgpt": {**base, **kw}})


def test_access_token_still_valid(auth):
    assert CS.access_token(signed()) == "AT1" and tokens(auth) == []


def test_access_token_renewed_when_running_out(auth):
    c = signed(expires_at=time.time() + 60)
    assert CS.access_token(c) == "AT-new"
    assert tokens(auth) == [{"grant_type": "refresh_token", "client_id": "oaiapp_123", "refresh_token": "R1",
                             "resource": "https://api.openai.com/v1"}]
    saved = c.get("chatgpt")
    assert (saved["refresh_token"], saved["id_token"]) == ("R2", "old")
    assert saved["expires_at"] > time.time() + 3500


def test_forced_renewal_waits_for_the_earliest_refresh(auth):
    assert CS.access_token(signed(earliest_refresh_at=time.time() + 600), force=True) == "AT1"
    assert tokens(auth) == []
    assert CS.access_token(signed(), force=True) == "AT-new"


def test_renewal_keeps_the_refresh_token_and_takes_a_new_id_token(auth):
    auth.http.on("POST", "/auth/token", Json(200, {"access_token": "AT3", "id_token": "new-id"}))
    c = signed(expires_at=0)
    CS.access_token(c)
    assert (c.get("chatgpt")["refresh_token"], c.get("chatgpt")["id_token"]) == ("R1", "new-id")


@pytest.mark.parametrize("answer", [Json(400, {"error": "invalid_grant"}), Json(401, {"error": "x"}),
                                    Json(200, {"error": {"code": "refresh_token_reused"}})])
def test_a_dead_refresh_token_signs_out(auth, answer):
    auth.http.on("POST", "/auth/token", answer)
    c = signed(expires_at=0)
    with pytest.raises(CS.SignInError, match="ran out - sign in again"):
        CS.access_token(c)
    assert c.get("chatgpt") == {"client_id": "oaiapp_123", "id_token": "old", "earliest_refresh_at": 0}
    assert not CS.signed_in(c)


def test_renewal_server_trouble(auth):
    auth.http.on("POST", "/auth/token", Json(503, {"error": {"type": "server_error"}}))
    with pytest.raises(CS.SignInError, match=r"Couldn't renew the ChatGPT sign-in \(server_error\)"):
        CS.access_token(signed(expires_at=0))


def test_renewal_offline(monkeypatch):
    def post(*a, **kw):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(CS.requests, "post", post)
    with pytest.raises(CS.SignInError, match="Couldn't reach OpenAI"):
        CS.access_token(signed(expires_at=0))


def test_not_signed_in():
    with pytest.raises(CS.SignInError, match="Not signed in"):
        CS.access_token(MemoryCredentials({}))
    assert not CS.signed_in(MemoryCredentials({"chatgpt": {"access_token": "a"}}))


# ------------------------------------------------------------------ signing out
@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(CS, "time", SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=slept.append))
    return slept


def test_sign_out_revokes_and_keeps_the_registration(auth, no_sleep):
    c = signed(validated=True)
    assert CS.sign_out(c) is True
    assert [r.form() for r in auth.http.sent("/auth/revoke")] == [
        {"token": "R1", "token_type_hint": "refresh_token", "client_id": "oaiapp_123"}]
    assert c.get("chatgpt") == {"client_id": "oaiapp_123", "validated": True} and no_sleep == [0]


def test_sign_out_refused_by_openai(auth, no_sleep):
    auth.revoke_status = 400
    c = signed()
    assert CS.sign_out(c) is False and len(auth.http.sent("/auth/revoke")) == 1
    assert c.get("chatgpt") == {"client_id": "oaiapp_123", "validated": True}


def test_sign_out_retries_server_trouble(auth, no_sleep):
    auth.revoke_status = 502
    assert CS.sign_out(signed()) is False
    assert len(auth.http.sent("/auth/revoke")) == 3 and no_sleep == [0, 1, 3]


def test_sign_out_without_tokens(auth, no_sleep):
    c = MemoryCredentials({"chatgpt": {"client_id": "x"}})
    assert CS.sign_out(c) is True and auth.http.requests == [] and c.get("chatgpt") is None
