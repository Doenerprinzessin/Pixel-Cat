"""Sign in with GitHub (for Copilot): GitHub's OAuth device flow, with Pixel Cat's own OAuth app.

* GitHub's documented device flow (docs.github.com: "Authorizing OAuth apps" -> device flow):
  POST /login/device/code -> the user enters the shown code on github.com/login/device ->
  Pixel Cat polls /login/oauth/access_token until it is approved.  No client secret, no local
  web server.
* ``CLIENT_ID`` is Pixel Cat's own GitHub OAuth app (Device Flow enabled) - never another
  app's.  While it is empty, the "Sign in with GitHub" button is hidden and only a token
  (fine-grained, with the Copilot Requests permission) can be used.
* The token goes into keys.dat (``copilot_signin``) and is only ever sent to GitHub.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import requests

from ...llm.external import Credentials

log = logging.getLogger(__name__)

CLIENT_ID = ""      # Pixel Cat's own GitHub OAuth app (Settings -> Developer settings -> OAuth Apps)
SCOPE = "read:user"
URLS = {"device": "https://github.com/login/device/code",
        "token": "https://github.com/login/oauth/access_token",
        "user": "https://api.github.com/user"}
_HDR = {"Accept": "application/json", "User-Agent": "PixelCat"}


class SignInError(Exception):
    """The message is for the user."""


def available() -> bool:
    return bool(CLIENT_ID)


def signed_in(creds: Credentials) -> bool:
    s = creds.get("copilot_signin")
    return isinstance(s, dict) and bool(s.get("token"))


def start() -> dict[str, Any]:
    """A new device code: user_code, verification_uri, device_code, interval, expires_in."""
    if not CLIENT_ID:
        raise SignInError("Sign in with GitHub isn't set up in this version - use a token instead.")
    try:
        r = requests.post(URLS["device"], data={"client_id": CLIENT_ID, "scope": SCOPE}, headers=_HDR, timeout=30)
    except requests.RequestException as e:
        raise SignInError("I couldn't reach GitHub. Is the internet on?") from e
    data = _json(r)
    if r.status_code >= 400 or not data.get("device_code"):
        raise SignInError(f"GitHub didn't start the sign-in ({data.get('error_description') or data.get('error') or r.status_code}).")
    return data


def poll(dev: dict[str, Any], cancel: threading.Event | None = None) -> str:
    """Wait until the user has approved the code on github.com; returns the access token."""
    interval = max(1, int(dev.get("interval") or 5))
    end = time.time() + int(dev.get("expires_in") or 900)
    while time.time() < end:
        if cancel is not None and cancel.wait(interval):
            raise SignInError("cancelled")
        if cancel is None:
            time.sleep(interval)
        try:
            r = requests.post(URLS["token"], headers=_HDR, timeout=30, data={
                "client_id": CLIENT_ID, "device_code": dev["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        except requests.RequestException:
            continue                                 # (a blip: try again at the next interval)
        data = _json(r)
        if data.get("access_token"):
            return str(data["access_token"])
        err = str(data.get("error") or "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval = int(data.get("interval") or interval + 5)
            continue
        if err == "expired_token":
            break
        if err == "access_denied":
            raise SignInError("The sign-in was declined on GitHub.")
        raise SignInError(f"GitHub refused the sign-in ({data.get('error_description') or err or r.status_code}).")
    raise SignInError("The code ran out before it was entered - try again.")


def user(token: str) -> str:
    """The GitHub login the token belongs to ("" if GitHub doesn't say)."""
    try:
        r = requests.get(URLS["user"], headers={**_HDR, "Authorization": f"Bearer {token}"}, timeout=30)
        return str(_json(r).get("login") or "") if r.status_code == 200 else ""
    except requests.RequestException:
        return ""


def sign_in(creds: Credentials, show_code: Callable[[str, str], None],
            cancel: threading.Event | None = None) -> dict[str, Any]:
    """The whole sign-in: ``show_code(user_code, verification_uri)`` tells the user what to do
    (from this thread).  Keeps the token in keys.dat; returns {"login": ...}."""
    dev = start()
    show_code(str(dev.get("user_code") or ""), str(dev.get("verification_uri") or "https://github.com/login/device"))
    token = poll(dev, cancel)
    login = user(token)
    creds.set("copilot_signin", {"token": token, "login": login, "at": time.time()})
    log.info("signed in with GitHub%s", f" as {login}" if login else "")
    return {"login": login}


def sign_out(creds: Credentials) -> None:
    """Forget the token.  (Revoking it needs GitHub's settings: Applications -> Authorized OAuth Apps.)"""
    creds.set("copilot_signin", None)


def _json(r: requests.Response) -> dict[str, Any]:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
