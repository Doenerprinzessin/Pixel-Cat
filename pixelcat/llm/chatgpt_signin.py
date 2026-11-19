"""Sign in with ChatGPT - OpenAI's sign-in for open-source apps that run locally
(developers.openai.com/siwc): the user's ChatGPT plan pays for Pixel Cat's OpenAI requests.

The flow (OAuth 2.0 authorization code + PKCE, no client secret):
1. A host id for this PC (``urn:uuid:...``, made once, says nothing about the user).
2. A listener on ``http://127.0.0.1:1455/auth/callback`` (another port if that one is taken),
   then the browser opens auth.openai.com.  The first time the client id is
   ``dynamic_agent_client`` and OpenAI issues Pixel Cat its own one for this account.
3. The callback brings a code (and the issued client id); it's exchanged for tokens, the ID
   token is checked (signature against OpenAI's keys, issuer, audience, expiry, nonce) and
   so are the granted scopes.
4. Everything goes into keys.dat (encrypted for this Windows account).  The access token
   lasts an hour and is refreshed with the refresh token (30 days, renewed on every refresh).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .external import Credentials

log = logging.getLogger(__name__)

URLS = {"issuer": "https://auth.openai.com",
        "authorize": "https://auth.openai.com/api/accounts/authorize",
        "token": "https://auth.openai.com/api/accounts/oauth/token",
        "discovery": "https://auth.openai.com/.well-known/openid-configuration"}
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
RESOURCE = "https://api.openai.com/v1"
APP_NAME = "Pixel Cat"
PORT = 1455
DEAD_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                "refresh_token_invalidated", "refresh_token_reused"}
_refresh_lock = threading.Lock()


class SignInError(Exception):
    """The message is for the user."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def host_id(creds: Credentials) -> str:
    hid = creds.get("chatgpt_host")
    if not hid:
        hid = f"urn:uuid:{uuid.uuid4()}"
        creds.set("chatgpt_host", hid)
    return str(hid)


# ------------------------------------------------------------------ the ID token
def _rs256_ok(token: str, jwks: dict[str, Any]) -> bool | None:
    """RS256 signature check with the standard library (None: not an RS256 token)."""
    head_b64, body_b64, sig_b64 = token.split(".")
    header = json.loads(_unb64(head_b64))
    if header.get("alg") != "RS256":
        return None
    keys = [k for k in jwks.get("keys", []) if k.get("kty") == "RSA" and k.get("kid") in (header.get("kid"), None)]
    digest = hashlib.sha256(f"{head_b64}.{body_b64}".encode("ascii")).digest()
    info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    sig = int.from_bytes(_unb64(sig_b64), "big")
    for k in keys:
        n, e = int.from_bytes(_unb64(k["n"]), "big"), int.from_bytes(_unb64(k["e"]), "big")
        size = (n.bit_length() + 7) // 8
        if sig >= n:
            continue
        em = pow(sig, e, n).to_bytes(size, "big")
        if hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info):
            return True
    return False


def check_id_token(token: str, client_id: str, nonce: str | None) -> dict[str, Any]:
    """The ID token's claims, after checking signature, issuer, audience, expiry and nonce."""
    try:
        disc = requests.get(URLS["discovery"], timeout=20).json()
        jwks = requests.get(disc["jwks_uri"], timeout=20).json()
        claims = json.loads(_unb64(token.split(".")[1]))
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        raise SignInError("Couldn't check the sign-in with OpenAI - try again.") from e
    if _rs256_ok(token, jwks) is False:
        raise SignInError("OpenAI's answer didn't check out (bad signature), so I didn't use it.")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if (claims.get("iss") != disc.get("issuer", URLS["issuer"]) or client_id not in auds
            or float(claims.get("exp", 0)) < time.time() - 60 or (nonce and claims.get("nonce") != nonce)):
        raise SignInError("OpenAI's answer didn't check out, so I didn't use it. Try again.")
    return claims


# ------------------------------------------------------------------ signing in
def _listen() -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            self.server.result = {k: v[0] for k, v in parse_qs(url.query).items()}  # type: ignore[attr-defined]
            ok = "code" in self.server.result  # type: ignore[attr-defined]
            body = ("<h2>Pixel Cat is signed in with ChatGPT.</h2><p>You can close this tab now.</p>" if ok else
                    "<h2>Pixel Cat isn't signed in.</h2><p>You can close this tab and try again from the cat's menu.</p>")
            data = f"<html><body style='font-family:Segoe UI;padding:40px'>{body}</body></html>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_a: Any) -> None:
            pass

    for port in (PORT, 0):
        try:
            srv = HTTPServer(("127.0.0.1", port), Handler)
            srv.result = None  # type: ignore[attr-defined]
            srv.timeout = 0.5
            return srv
        except OSError:
            continue
    raise SignInError("Couldn't start the sign-in (no free port on this PC).")


def sign_in(creds: Credentials, open_url: Callable[[str], Any] = webbrowser.open,
            cancel: threading.Event | None = None, timeout: float = 300.0) -> dict[str, Any]:
    """The whole sign-in (blocking - run it in a thread).  Returns what was stored."""
    cancel = cancel or threading.Event()
    hid = host_id(creds)
    saved = creds.get("chatgpt") or {}
    client_id = saved.get("client_id") or "dynamic_agent_client"
    verifier = _b64(os.urandom(48))
    state, nonce = _b64(os.urandom(18)), _b64(os.urandom(18))
    srv = _listen()
    try:
        redirect = f"http://127.0.0.1:{srv.server_address[1]}/auth/callback"
        q = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
             "resource": RESOURCE, "state": state, "nonce": nonce, "ext_agent_host_id": hid,
             "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()),
             "code_challenge_method": "S256"}
        if client_id == "dynamic_agent_client":
            q["agent_name_hint"] = APP_NAME
        if saved.get("id_token"):
            q["id_token_hint"] = saved["id_token"]
        open_url(URLS["authorize"] + "?" + urlencode(q))
        end = time.monotonic() + timeout
        while srv.result is None:  # type: ignore[attr-defined]
            if cancel.is_set():
                raise SignInError("Sign-in cancelled.")
            if time.monotonic() > end:
                raise SignInError("Nobody signed in within 5 minutes - try again.")
            srv.handle_request()
        res: dict[str, str] = srv.result  # type: ignore[attr-defined]
    finally:
        srv.server_close()
    if res.get("state") != state:
        raise SignInError("The sign-in answer didn't match - try again.")
    if res.get("error"):
        raise SignInError("You said no to the sign-in." if res["error"] == "access_denied"
                          else f"The sign-in didn't work ({res['error']}).")
    issued = res.get("client_id") or (client_id if client_id != "dynamic_agent_client" else "")
    if not issued or (client_id != "dynamic_agent_client" and issued != client_id):
        raise SignInError("The sign-in answer didn't match - try again.")
    r = requests.post(URLS["token"], data={"grant_type": "authorization_code", "client_id": issued,
                                           "code": res.get("code", ""), "code_verifier": verifier,
                                           "redirect_uri": redirect, "resource": RESOURCE}, timeout=30)
    tok = _json(r)
    if r.status_code >= 400 or "access_token" not in tok:
        raise SignInError("The sign-in expired before it finished - try again." if _err(tok) == "invalid_grant"
                          else f"OpenAI didn't finish the sign-in ({_err(tok) or r.status_code}).")
    claims = check_id_token(tok.get("id_token", ""), issued, nonce)
    scopes = set(str(tok.get("scope") or res.get("scope") or "").split())
    info = {"client_id": issued, "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
            "id_token": tok.get("id_token", ""), "expires_at": time.time() + float(tok.get("expires_in") or 3600),
            "earliest_refresh_at": float(tok.get("earliest_refresh_at") or 0),
            "email": str(claims.get("email") or ""), "plan": PLAN_SCOPE in scopes}
    creds.set("chatgpt", info)
    log.info("signed in with ChatGPT (plan usage %s)", "on" if info["plan"] else "not granted")
    return info


def _json(r: requests.Response) -> dict[str, Any]:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _err(data: dict[str, Any]) -> str:
    e = data.get("error")
    if isinstance(e, dict):
        return str(e.get("code") or e.get("type") or "")
    return str(e or "")


# ------------------------------------------------------------------ using it
def signed_in(creds: Credentials) -> bool:
    c = creds.get("chatgpt") or {}
    return bool(c.get("access_token") and c.get("refresh_token"))


def access_token(creds: Credentials, force: bool = False) -> str:
    """A valid access token (refreshed when it is about to run out, or when ``force``)."""
    with _refresh_lock:
        c = dict(creds.get("chatgpt") or {})
        if not c.get("access_token"):
            raise SignInError("Not signed in with ChatGPT.")
        if not force and float(c.get("expires_at", 0)) - 120 > time.time():
            return str(c["access_token"])
        if force and float(c.get("earliest_refresh_at", 0)) > time.time():
            return str(c["access_token"])
        try:
            r = requests.post(URLS["token"], data={"grant_type": "refresh_token", "client_id": c["client_id"],
                                                   "refresh_token": c.get("refresh_token", ""),
                                                   "resource": RESOURCE}, timeout=30)
        except requests.RequestException as e:
            raise SignInError("Couldn't reach OpenAI to renew the sign-in.") from e
        tok = _json(r)
        if r.status_code >= 400 or "access_token" not in tok:
            if _err(tok) in DEAD_REFRESH or r.status_code in (400, 401):
                for k in ("access_token", "refresh_token", "expires_at"):
                    c.pop(k, None)
                creds.set("chatgpt", c)
                raise SignInError("The ChatGPT sign-in ran out - sign in again (Settings > AI model > "
                                  "External AI models > OpenAI).")
            raise SignInError(f"Couldn't renew the ChatGPT sign-in ({_err(tok) or r.status_code}).")
        c.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token") or c.get("refresh_token"),
                 expires_at=time.time() + float(tok.get("expires_in") or 3600),
                 earliest_refresh_at=float(tok.get("earliest_refresh_at") or 0))
        if tok.get("id_token"):
            c["id_token"] = tok["id_token"]
        creds.set("chatgpt", c)
        return str(c["access_token"])


def sign_out(creds: Credentials) -> bool:
    """Revoke the sign-in at OpenAI and forget the tokens (the issued client id and the host
    id stay for next time).  False: OpenAI couldn't confirm it (the user should disconnect
    Pixel Cat in ChatGPT's settings)."""
    c = dict(creds.get("chatgpt") or {})
    ok = True
    if c.get("refresh_token"):
        ok = False
        for wait in (0, 1, 3):
            time.sleep(wait)
            try:
                disc = requests.get(URLS["discovery"], timeout=15).json()
                r = requests.post(disc["revocation_endpoint"], data={"token": c["refresh_token"],
                                                                     "token_type_hint": "refresh_token",
                                                                     "client_id": c.get("client_id", "")}, timeout=15)
                if r.status_code == 200:
                    ok = True
                    break
                if r.status_code < 500:
                    break
            except (requests.RequestException, ValueError, KeyError):
                continue
    creds.set("chatgpt", {"client_id": c["client_id"]} if c.get("client_id") else None)
    return ok
