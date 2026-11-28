"""GitHub Copilot models through GitHub's official Copilot SDK (``microsoft-copilot-sdk``).

* The SDK drives GitHub's Copilot runtime (``copilot-runtime.exe``).  Pixel Cat gets it on
  first use from GitHub's official release page (github.com/github/copilot-cli - the SDK
  checks it against the release's SHA-256 sums) into %LOCALAPPDATA%\\PixelCat\\tools\\copilot:
  no installer, no admin rights, nothing on the PATH.  It runs as a hidden child process
  (crash-isolated from the cat) while Copilot models are in use.
* The sign-in is Pixel Cat's own: a fine-grained GitHub token (Copilot Requests permission) or
  "Sign in with GitHub" (github_signin.py) - kept in keys.dat and handed to the runtime for
  its process only.  The runtime runs in the SDK's "empty" mode, so it never uses the Copilot
  CLI's or the GitHub CLI's logins, the system keychain or GH_TOKEN & co., and keeps its state
  in %APPDATA%\\PixelCat\\copilot (not ~/.copilot).  Sessions are deleted when a chat ends.
* No Copilot tools of its own - except ``web_fetch`` (reading a public web page) when Web search
  is on, and every page it wants goes to the app first (``permission``: the confirmation card,
  or ALWAYS / auto mode as for the cat's read_web_page).  The cat's own tools are offered, and
  every call goes back to the app as ``done(text, calls)`` - the confirmation cards, admin rules
  and auto mode work exactly as for the other models.  The runtime keeps the conversation
  (``CopilotChat``: one session per chat); a tool call waits inside the runtime until the app
  hands over its result.
* Usage: tokens (the token limit) and AI credits (the credit limit) of every model call, and
  ``quota()`` - the Copilot plan's own monthly allowance.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ... import paths
from ...llm.external import Credentials, media_type
from ...ports.llm import ChatEvents, ChatRequest
from .chat_job import ExternalChat

log = logging.getLogger(__name__)

EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
MAX_DOWNLOAD = 400 * 1024 * 1024
IDLE_TIMEOUT = 15 * 60          # a turn that shows no sign of life for this long is given up
BYOK: dict[str, Any] | None = None   # (tests: a fake OpenAI-style server instead of GitHub - no account, no credits)
# Never handed to the runtime: other apps' GitHub logins and Copilot settings.
_DROP_ENV = {"GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "GITHUB_COPILOT_API_TOKEN", "COPILOT_API_URL",
             "COPILOT_HOME", "COPILOT_CLI_PATH", "COPILOT_SDK_AUTH_TOKEN", "COPILOT_PROVIDER_BASE_URL",
             "COPILOT_PROVIDER_API_KEY", "COPILOT_SDK_DEFAULT_CONNECTION"}


class CopilotError(Exception):
    """The message is for the user.  ``kind``: auth, ext_offline, install, denied, error."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ------------------------------------------------------------------ where things live
def tools_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "PixelCat" / "tools" / "copilot"


def home_dir() -> Path:
    """The runtime's own folder (COPILOT_HOME): its settings and the running chats."""
    path = paths.data_dir() / "copilot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _work_dir() -> Path:
    """An empty working folder: the runtime finds no project files or instructions there."""
    path = home_dir() / "workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dl() -> Any:
    """The SDK's runtime download, pointed at Pixel Cat's tools folder (this process only -
    nothing changes in the environment that apps the cat opens inherit)."""
    from copilot import _cli_download as dl

    if not getattr(dl, "_pixelcat", False):
        dl.get_cache_dir = lambda version=None: tools_dir() / (dl._sanitize_version(version) if version else "")
        dl._pixelcat = True
    return dl


def runtime_path() -> str | None:
    """GitHub's Copilot runtime, if it is here and complete."""
    try:
        dl = _dl()
        from copilot._cli_version import CLI_VERSION, get_runtime_platform

        if not CLI_VERSION:
            return None
        pair = dl.get_cache_dir(CLI_VERSION) / "prebuilds" / get_runtime_platform()
        wrapper = pair / "copilot-runtime.exe"
        complete = getattr(dl, "_runtime_bundle_is_complete", None)
        ok = complete(pair, wrapper.name) if complete else (wrapper.is_file() and (pair / "runtime.node").is_file())
        return str(wrapper) if ok else None
    except Exception:  # noqa: BLE001 - no SDK, unsupported platform ...
        log.debug("no Copilot runtime", exc_info=True)
        return None


_install_lock = threading.Lock()


def install(progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> str:
    """Get GitHub's Copilot runtime (about 100 MB, checked by the SDK); returns its path."""
    with _install_lock:
        have = runtime_path()
        if have:
            return have
        dl = _dl()
        orig = dl._fetch_url_bytes

        def fetch(url: str, *, timeout: int) -> bytes:   # the SDK's download, with progress and cancel
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "PixelCat"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        total = int(r.headers.get("Content-Length") or 0)
                        buf = bytearray()
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise CopilotError("install", "Cancelled.")
                            chunk = r.read(256 * 1024)
                            if not chunk:
                                return bytes(buf)
                            buf += chunk
                            if len(buf) > MAX_DOWNLOAD:
                                raise CopilotError("install", "The download is far too big - I stopped it.")
                            if progress is not None and total > 1_000_000:
                                progress(len(buf), total)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                    if attempt == 2:
                        raise CopilotError("ext_offline", f"I couldn't reach GitHub ({e}).") from e
                    time.sleep(2 ** attempt)
            raise CopilotError("ext_offline", "I couldn't reach GitHub.")

        dl._fetch_url_bytes = fetch
        try:
            path = dl.ensure_runtime_wrapper()
        except CopilotError:
            raise
        except Exception as e:  # noqa: BLE001 - a failed checksum, a full disk ...
            raise CopilotError("install", f"I couldn't set up GitHub's Copilot runtime: {e}") from e
        finally:
            dl._fetch_url_bytes = orig
        log.info("Copilot runtime set up in %s (checked against GitHub's checksums)", Path(path).parent)
        return path


# ------------------------------------------------------------------ the runtime (one process)
class _Loop:
    """The SDK is asyncio: it runs on one background thread with its own event loop."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def get(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self.loop is None or self.loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="pixelcat-copilot", daemon=True).start()
                self.loop = loop
            return self.loop

    def submit(self, coro: Any) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.get())

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        return self.submit(coro).result(timeout)


_LOOP = _Loop()


class _Runtime:
    """The SDK client - one runtime process - for the token in use."""

    def __init__(self) -> None:
        self.client: Any = None
        self.token: str | None = None
        self._lock: asyncio.Lock | None = None

    async def get(self, token: str) -> Any:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.client is not None and self.token == token:
                return self.client
            await self._stop()
            client = await _make_client(token)
            self.client, self.token = client, token
            log.info("Copilot runtime started")
            try:                                 # chats of an earlier run (the cat keeps none)
                for s in await client.list_sessions():
                    await client.delete_session(s.session_id)
            except Exception:  # noqa: BLE001
                log.debug("old Copilot sessions not cleared", exc_info=True)
            _prune_logs()
            return client

    async def _stop(self) -> None:
        client, self.client, self.token = self.client, None, None
        if client is not None:
            await _stop_client(client)
            log.info("Copilot runtime stopped")

    async def stop(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            await self._stop()


def new_client(token: str, path: str) -> Any:
    """An SDK client for this token (not started): the runtime at ``path`` as a hidden child
    process, "empty" mode, Pixel Cat's own folder, no other logins."""
    from copilot import CopilotClient, RuntimeConnection

    env = {k: v for k, v in os.environ.items() if k.upper() not in _DROP_ENV}
    env["COPILOT_TELEMETRY_DISABLED"] = "true"
    gh: dict[str, Any] = {"use_logged_in_user": False}
    if token:
        gh["token"] = token
    return CopilotClient(mode="empty", github=gh, runtime=RuntimeConnection.for_spawned_stdio(
        path=path, env=env, base_directory=str(home_dir()), working_directory=str(_work_dir())))


async def _make_client(token: str) -> Any:
    path = runtime_path()
    if path is None:
        raise CopilotError("install", "GitHub's Copilot runtime isn't set up yet.")
    client = new_client(token, path)
    try:
        await client.start()
    except Exception as e:  # noqa: BLE001
        raise CopilotError("error", f"GitHub's Copilot runtime didn't start ({e}).") from e
    return client


async def _stop_client(client: Any) -> None:
    try:
        await asyncio.wait_for(client.stop(), 12)
    except Exception:  # noqa: BLE001
        try:
            await client.force_stop()
        except Exception:  # noqa: BLE001
            pass


def _prune_logs(days: int = 14) -> None:
    """The runtime writes a small log per start; old ones go."""
    cutoff = time.time() - days * 86400
    for f in (home_dir() / "logs").glob("process-*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


_RT = _Runtime()


def shutdown(timeout: float = 8.0) -> None:
    """Stop the runtime (quitting, signing out)."""
    if _RT.client is None:
        return
    try:
        _LOOP.run(_RT.stop(), timeout)
    except Exception:  # noqa: BLE001
        log.warning("the Copilot runtime didn't stop cleanly", exc_info=True)


def token_of(creds: Credentials, auth: str) -> str:
    if BYOK is not None:
        return ""
    if auth == "signin":
        s = creds.get("copilot_signin")
        tok = str(s.get("token") or "") if isinstance(s, dict) else ""
    else:
        tok = str(creds.get("copilot_key") or "")
    if not tok:
        raise CopilotError("auth", "No GitHub sign-in or token saved.")
    return tok


def _error_of(exc: BaseException) -> CopilotError:
    if isinstance(exc, CopilotError):
        return exc
    low = str(exc).lower()
    if any(w in low for w in ("401", "unauthori", "bad credentials", "not authenticated", "authentication",
                              "token is invalid", "invalid token")):
        return CopilotError("auth", str(exc))
    if any(w in low for w in ("403", "not entitled", "no access", "forbidden", "not enabled", "copilot access")):
        return CopilotError("denied", str(exc))
    if any(w in low for w in ("getaddrinfo", "connect", "network", "timed out", "unreachable", "dns")):
        return CopilotError("ext_offline", str(exc))
    return CopilotError("error", str(exc))


# ------------------------------------------------------------------ account: models, quota, sign-in check
async def _models_of(client: Any) -> list[dict[str, Any]]:
    if BYOK is not None:
        return [dict(m) for m in BYOK.get("models", [])]
    try:
        ms = await client.list_models()
    except Exception as e:  # noqa: BLE001
        raise _error_of(e) from e
    out = []
    for m in ms:
        state = getattr(getattr(m, "policy", None), "state", None)
        if getattr(state, "value", state) == "disabled":
            continue                                 # (turned off for this account / organization)
        sup = getattr(getattr(m, "capabilities", None), "supports", None)
        if sup is not None and sup.tool_calls is False:
            continue                                 # (the cat needs its tools)
        efforts = list(getattr(m, "supported_reasoning_efforts", None) or []) if sup is None or sup.reasoning_effort \
            else []
        out.append({"id": m.id, "name": m.name or m.id, "efforts": efforts, "vision": bool(sup and sup.vision)})
    return out


def list_models(creds: Credentials, auth: str) -> list[dict[str, Any]]:
    """The Copilot models this account may use (the organization's policy decides)."""
    async def go() -> list[dict[str, Any]]:
        client = None if BYOK is not None else await _RT.get(token_of(creds, auth))
        return await _models_of(client)
    return _LOOP.run(go(), 180)


async def _login_of(client: Any) -> str:
    if BYOK is not None:
        return str(BYOK.get("login", ""))
    try:
        st = await client.get_auth_status()
    except Exception as e:  # noqa: BLE001
        raise _error_of(e) from e
    if not st.is_authenticated:
        raise CopilotError("auth", st.status_message or "GitHub didn't accept that token.")
    return st.login or ""


def check_token(token: str) -> tuple[str, list[dict[str, Any]]]:
    """A token that is only being tried (its own short-lived runtime, so a running chat isn't
    disturbed): (the GitHub login, the Copilot models) - or CopilotError."""
    async def go() -> tuple[str, list[dict[str, Any]]]:
        client = None if BYOK is not None else await _make_client(token)
        try:
            login = await _login_of(client)
            ms = await _models_of(client)
        finally:
            if client is not None:
                await _stop_client(client)
        if not ms:
            raise CopilotError("denied", "That token can't use Copilot models.")
        return login, ms
    return _LOOP.run(go(), 180)


def pick_quota(snaps: dict[str, Any]) -> tuple[str, Any] | None:
    """The quota that matters for chats: AI credits, else premium requests, else the chat one."""
    if not snaps:
        return None
    for want in ("credit", "aic", "premium", "chat"):
        for k, s in snaps.items():
            if want in k.lower():
                return k, s
    return next(iter(snaps.items()))


async def _quota(token: str, start: bool) -> dict[str, Any] | None:
    if BYOK is not None:
        q = BYOK.get("quota")
        return dict(q, at=time.time()) if isinstance(q, dict) else None
    if not start and (_RT.client is None or _RT.token != token):
        return None                                  # (not worth starting the runtime for)
    client = await _RT.get(token)
    from copilot._generated.rpc import AccountGetQuotaRequest

    res = await client.rpc.account.get_quota(AccountGetQuotaRequest())
    hit = pick_quota(res.quota_snapshots or {})
    if hit is None:
        return None
    key, s = hit
    total = float(s.entitlement_requests or 0)
    used = float(s.used_requests or 0)
    if total > 0 and s.remaining_percentage is not None:     # (the percentage has the decimals)
        used = max(0.0, total * (100.0 - float(s.remaining_percentage)) / 100.0)
    return {"kind": key, "used": round(used, 1), "total": total, "unlimited": bool(s.is_unlimited_entitlement),
            "reset": s.reset_date or "", "overage_ok": bool(s.overage_allowed_with_exhausted_quota), "at": time.time()}


def quota(creds: Credentials, auth: str, start: bool = False) -> dict[str, Any] | None:
    """The Copilot plan's own allowance: used / total, when it resets (None: not known).
    Only while the runtime runs anyway, unless ``start``."""
    try:
        return _LOOP.run(_quota(token_of(creds, auth), start), 60)
    except Exception:  # noqa: BLE001 - only information
        log.info("Copilot quota not available", exc_info=True)
        return None


# ------------------------------------------------------------------ one chat
def pick_effort(think: bool, effort: str, efforts: list[str]) -> str | None:
    """The cat's thinking/effort settings for a model with these efforts (None: the model's own)."""
    known = [e for e in efforts or [] if e in EFFORT_ORDER]
    if not known:
        return None
    if not think:
        return min(known, key=EFFORT_ORDER.index)
    want = EFFORT_ORDER.index(effort if effort in EFFORT_ORDER else "medium")
    below = [e for e in known if EFFORT_ORDER.index(e) <= want]
    return max(below, key=EFFORT_ORDER.index) if below else min(known, key=EFFORT_ORDER.index)


def _is_result(m: dict[str, Any]) -> bool:
    return m.get("role") == "tool" or (m.get("role") == "user" and bool(m.get("_tool"))
                                       and str(m.get("content", "")).startswith("[tool result for "))


def _result_text(m: dict[str, Any]) -> str:
    text = str(m.get("content") or "")
    if m.get("role") == "user" and text.startswith("[tool result for "):
        text = text.split("] ", 1)[-1]
    return text or "(done)"


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + " …"


def compose(msgs: list[dict[str, Any]]) -> str:
    """What the runtime hasn't seen yet as one message: the user's latest message(s), after a
    short account of anything else that happened meanwhile (another model's replies, tool
    results without a waiting call, an earlier part of the chat)."""
    latest: list[str] = []
    i = len(msgs)
    while i > 0 and msgs[i - 1].get("role") == "user" and not _is_result(msgs[i - 1]):
        i -= 1
        text = str(msgs[i].get("content") or "").strip()
        if msgs[i].get("images"):
            text += " (a picture was attached - I can't see it here)"
        if text:
            latest.insert(0, text)
    lines: list[str] = []
    talk = False
    for m in msgs[:i]:
        role, text = m.get("role"), str(m.get("content") or "").strip()
        if _is_result(m):
            lines.append(f"[result of {m.get('tool_name', 'a tool')}] {_clip(_result_text(m), 600)}")
        elif role == "user" and text:
            talk = True
            lines.append(f"User: {_clip(text, 1200)}")
        elif role == "assistant":
            calls = ", ".join(str((tc.get("function") or {}).get("name", "")) for tc in m.get("tool_calls") or [])
            if text or calls:
                talk = True
                lines.append(f"You: {_clip(text, 1200)}" + (f" [used: {calls}]" if calls else ""))
    while sum(len(x) for x in lines) > 14_000 and len(lines) > 1:
        lines.pop(0)                                 # (the oldest part goes first)
    head = ""
    if lines:
        head = ("(Earlier in this chat - for context, already done:)\n" if talk else "") + "\n".join(lines)
    body = "\n\n".join(latest) or "(continue)"
    return f"{head}\n\n(Now:)\n{body}" if head and talk else (f"{head}\n\n{body}" if head else body)


class CopilotChat:
    """One chat with Copilot models: the runtime's session, how much of the app's history it has
    seen, and the tool calls waiting inside the runtime for their results.  Everything here runs
    on the SDK's thread (``_LOOP``)."""

    def __init__(self) -> None:
        self.session: Any = None
        self.client: Any = None                      # the runtime the session lives in
        self.session_id = ""
        self.model = ""
        self.effort: str | None = None
        self.tool_names: set[str] = set()
        self.consumed = 0                            # app history items the runtime has
        self.expect_reply = False                    # the app's next new item is our own reply
        self.pending: dict[str, asyncio.Future] = {}
        self.order: list[str] = []                   # the waiting calls, in the order the app runs them
        self.queue: asyncio.Queue | None = None
        self.turn = False                            # a turn is running in the runtime
        self.web = False                             # the runtime's own web_fetch may be used (Settings: Web search)
        self.web_tool = False                        # ...the session was opened with it
        self.perms: list[asyncio.Future] = []        # web pages waiting for the user's OK

    # -- the session --------------------------------------------------------------------------
    def _effort(self, w: "CopilotJob") -> str | None:
        return pick_effort(w.think, w.effort, (w.info or {}).get("efforts") or [])

    @staticmethod
    def _model_name(model: str) -> str:
        return f"{BYOK['provider']['name']}/{model}" if BYOK is not None else model

    async def _open(self, w: "CopilotJob", client: Any) -> None:
        from copilot.tools import Tool

        defs = []
        for t in w.tools or []:
            fn = t.get("function") or t
            defs.append(Tool(name=fn["name"], description=fn.get("description", ""), handler=self._handler(fn["name"]),
                             parameters=fn.get("parameters") or {"type": "object", "properties": {}},
                             skip_permission=True))
        self.tool_names = {d.name for d in defs}
        self.web = self.web_tool = bool(w.web)
        effort = self._effort(w)
        kw: dict[str, Any] = dict(
            model=self._model_name(w.model), client_name="Pixel Cat",
            system_message={"mode": "replace", "content": w.system},
            tools={"definitions": defs, "available_tools": sorted(self.tool_names | ({"web_fetch"} if self.web else set()))},
            events={"streaming": True}, permissions={"on_request": self._permission},
            github={"telemetry_enabled": False}, working_directory=str(_work_dir()),
            agents={"included_builtin_agents": []}, skills={"enabled": False},
            instructions={"skip_custom_instructions": True}, state={"enable_cross_session_store": False},
            enable_config_discovery=False)
        if effort:
            kw["inference"] = {"reasoning_effort": effort}
        if BYOK is not None:
            from copilot._generated.rpc import ModelCapabilitiesOverride, ModelCapabilitiesOverrideSupports

            prov = BYOK["provider"]
            kw["inference"] = {**kw.get("inference", {}), "providers": [prov], "default_provider": prov["name"],
                               "models": [{"id": m["id"], "provider": prov["name"], "capabilities": ModelCapabilitiesOverride(
                                   supports=ModelCapabilitiesOverrideSupports(vision=bool(m.get("vision")), tool_calls=True))}
                                   for m in BYOK.get("models", [])]}
            kw["github"]["capi"] = {"enabled": False}
        self.queue = asyncio.Queue()
        session = await client.create_session(**kw)
        q, loop = self.queue, asyncio.get_running_loop()
        session.on(lambda ev, q=q: loop.call_soon_threadsafe(q.put_nowait, ("ev", ev)))
        self.session, self.session_id, self.client = session, session.session_id, client
        self.model, self.effort = w.model, effort
        self.consumed, self.expect_reply, self.turn = 0, False, False
        log.info("Copilot chat started (%s%s)", w.model, f", effort {effort}" if effort else "")

    def _handler(self, name: str) -> Callable[[Any], Any]:
        async def handle(inv: Any) -> Any:
            fut = asyncio.get_running_loop().create_future()
            cid = inv.tool_call_id or f"call_{len(self.pending)}"
            self.pending[cid] = fut
            if self.queue is not None:
                self.queue.put_nowait(("tool", cid, name, inv.arguments))
            try:
                return await fut
            finally:
                self.pending.pop(cid, None)
        return handle

    async def _permission(self, request: Any, invocation: Any) -> Any:
        """The runtime asks before its own tools act.  Only web_fetch (reading a public web page,
        when Web search is on) may - and only once the app said yes (the confirmation card, or
        ALWAYS / auto mode, as for read_web_page).  Everything else is refused."""
        from copilot._generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject

        if getattr(request, "kind", None) != "url" or not self.web or self.queue is None:
            return _refuse(request, invocation)
        url = str(getattr(request, "url", "") or "")
        from ...llm import webpage

        try:
            await asyncio.to_thread(webpage.check_address, url)
        except webpage.WebError as e:
            log.info("Copilot's web_fetch refused: %s", e)
            return PermissionDecisionReject(feedback=str(e))
        fut = asyncio.get_running_loop().create_future()
        self.perms.append(fut)
        self.queue.put_nowait(("perm", fut, url))
        try:
            ok = bool(await fut)
        except asyncio.CancelledError:
            ok = False
        finally:
            if fut in self.perms:
                self.perms.remove(fut)
        return PermissionDecisionApproveOnce() if ok else \
            PermissionDecisionReject(feedback="The user didn't allow reading this page.")

    def _drop_perms(self) -> None:
        for f in self.perms:
            if not f.done():
                f.cancel()
        self.perms.clear()

    async def _close(self) -> None:
        session, self.session = self.session, None
        sid, self.session_id = self.session_id, ""
        client, self.client = self.client, None
        for f in self.pending.values():
            f.cancel()
        self.pending.clear()
        self._drop_perms()
        self.order.clear()
        self.consumed, self.expect_reply, self.turn = 0, False, False
        if session is None:
            return
        for step in (session.abort, session.disconnect):
            try:
                await asyncio.wait_for(step(), 5)
            except Exception:  # noqa: BLE001
                pass
        try:
            if client is not None and client is _RT.client and sid:
                await asyncio.wait_for(client.delete_session(sid), 5)   # (the cat keeps no chats)
        except Exception:  # noqa: BLE001
            pass

    def close_soon(self) -> None:
        """The chat is over (new chat, sign-out): drop the session in the background."""
        _LOOP.submit(self._close())

    def close(self, timeout: float = 6.0) -> None:
        """The same, waiting for it (quitting)."""
        if self.session is None:
            return
        try:
            _LOOP.run(self._close(), timeout)
        except Exception:  # noqa: BLE001
            log.debug("Copilot chat not closed cleanly", exc_info=True)

    async def abort(self) -> None:
        """Stop the running turn; calls (and web pages) waiting for an answer are dropped."""
        if self.session is None or not (self.turn or self.pending or self.perms):
            return
        self._drop_perms()
        try:
            await asyncio.wait_for(self.session.abort(), 5)
        except Exception:  # noqa: BLE001
            pass
        for f in self.pending.values():
            f.cancel()
        self.pending.clear()
        self.order.clear()
        end = time.time() + 5
        while self.turn and time.time() < end:       # (until the runtime says it's idle)
            await self._next(None, 0.25)
        self.turn = False

    def abort_soon(self) -> None:
        _LOOP.submit(self.abort())

    # -- one request -----------------------------------------------------------------------------
    async def step(self, w: "CopilotJob", token: str) -> None:
        """What the app has that the runtime hasn't (results, messages) goes in; then wait until
        the model wants tools (``done(text, calls)``) or has finished (``done(text, [])``)."""
        hist = w.history
        client = await _RT.get(token)
        if self.session is not None and (self.client is not client or self.consumed > len(hist)):
            await self._close()                      # (another sign-in, a restarted runtime, a cleared chat)
        if self.session is None:
            await self._open(w, client)
        elif w.model != self.model or self._effort(w) != self.effort:
            effort = self._effort(w)
            await self.session.set_model(self._model_name(w.model), reasoning_effort=effort)
            self.model, self.effort = w.model, effort
        self.web = self.web_tool and bool(w.web)     # (Web search switched off: web_fetch is refused)
        while self.queue is not None and not self.queue.empty():
            await self._next(w, 0)                   # (left over: usage after a reply, idle after an error)
        new = list(hist[self.consumed:])
        self.consumed = len(hist)
        if self.expect_reply and new and new[0].get("role") == "assistant":
            new = new[1:]                            # (our own reply, as the app keeps it)
        self.expect_reply = False
        results = [m for m in new if _is_result(m)]
        others = [m for m in new if not _is_result(m)]
        if self.pending and not others:
            self._answer(results)
        else:
            if self.pending:
                await self.abort()                   # (a stopped task, a new message: the old calls are dropped)
            prompt = compose(new)
            self.turn = True
            await self.session.send(prompt)
        await self._wait(w)

    def _answer(self, results: list[dict[str, Any]]) -> None:
        from copilot.tools import ToolBinaryResult, ToolResult

        ids = [c for c in self.order if c in self.pending] + [c for c in self.pending if c not in self.order]
        for i, cid in enumerate(ids):
            fut = self.pending.get(cid)
            if fut is None or fut.done():
                continue
            m = results[i] if i < len(results) else None
            if m is None:
                fut.set_result(ToolResult(text_result_for_llm="(not run - the user stopped it)"))
                continue
            pics = [ToolBinaryResult(data=b, mime_type=media_type(b), type="image",
                                     description=f"the picture from {m.get('tool_name') or 'the tool'}")
                    for b in m.get("images") or [] if isinstance(b, str) and b]
            fut.set_result(ToolResult(text_result_for_llm=_result_text(m), binary_results_for_llm=pics or None))
        self.order.clear()

    async def _next(self, w: "CopilotJob | None", timeout: float) -> tuple | None:
        """One event from the runtime (None: nothing within ``timeout``).  Usage is counted
        here, whoever is waiting."""
        assert self.queue is not None
        try:
            item = self.queue.get_nowait() if timeout <= 0 else await asyncio.wait_for(self.queue.get(), timeout)
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None
        if item[0] == "perm" and (w is None or w._cancel):
            item[1].cancel()                         # (nobody to ask any more: not read)
        if item[0] == "ev":
            t = _etype(item[1])
            d = item[1].data
            if t == "assistant.usage" and w is not None:
                tokens = int(d.input_tokens or 0) + int(d.output_tokens or 0)
                if tokens:
                    w.events.used("copilot", tokens)
                cu = d.copilot_usage
                if cu is not None and cu.total_nano_aiu:
                    w.events.credits(float(cu.total_nano_aiu) / 1e9)
            elif t == "session.idle":
                self.turn = False
        return item

    async def _wait(self, w: "CopilotJob") -> None:  # noqa: C901 - one event switch
        texts: list[str] = []
        deltas: list[str] = []
        thought = 0
        expected: list[str] | None = None
        calls: dict[str, dict[str, Any]] = {}
        quiet_since = time.time()
        while True:
            if w._cancel:
                return
            item = await self._next(w, 0.25)
            if item is None:
                if time.time() - quiet_since > IDLE_TIMEOUT:
                    await self.abort()
                    w.events.failed("timeout", "")
                    return
                continue
            quiet_since = time.time()
            if item[0] == "tool":
                _, cid, name, args = item
                calls[cid] = {"id": cid, "name": name, "arguments": args if isinstance(args, dict) else {}}
            elif item[0] == "perm":
                _, fut, url = item
                if not fut.done():
                    loop = asyncio.get_running_loop()

                    def reply(ok: bool, f: asyncio.Future = fut) -> None:     # (from the app's thread)
                        loop.call_soon_threadsafe(lambda: f.done() or f.set_result(bool(ok)))
                    w.events.permission(url, reply)
            else:
                ev = item[1]
                if getattr(ev, "agent_id", None):
                    continue                         # (sub-agents: none are allowed, to be safe)
                t, d = _etype(ev), ev.data
                if t == "assistant.message_delta":
                    piece = d.delta_content or ""
                    if piece:
                        deltas.append(piece)
                        w.events.token(piece)
                elif t == "assistant.reasoning_delta":
                    thought += len(d.delta_content or "") or 1
                    w.events.thinking(thought)
                elif t == "tool.execution_start" and getattr(d, "tool_name", "") == "web_fetch":
                    w.events.status("🌐 reading a web page…")
                elif t == "assistant.message":
                    text = d.content if d.content is not None else "".join(deltas)
                    if text.strip():
                        texts.append(text)
                    deltas = []
                    reqs = [r for r in d.tool_requests or [] if r.name in self.tool_names]
                    if reqs:
                        expected = [r.tool_call_id for r in reqs]
                elif t == "session.error":
                    self.order.clear()
                    _fail(w, d)
                    return
                elif t == "session.idle":
                    self.turn = False
                    self.expect_reply = True
                    w.events.done("\n\n".join(texts) or "".join(deltas), [])
                    return
            if expected and all(c in calls for c in expected):
                self.order = list(expected)
                self.expect_reply = True
                w.events.done("\n\n".join(texts), [calls[c] for c in expected])
                return


def _etype(ev: Any) -> str:
    t = getattr(ev, "type", "")
    return str(getattr(t, "value", t))


def _refuse(request: Any, invocation: Any) -> Any:
    """Nothing but the cat's own tools may run - and those never ask here (the app asks)."""
    from copilot._generated.rpc import PermissionDecisionReject

    log.warning("Copilot asked for a permission (%s) - refused", type(request).__name__)
    return PermissionDecisionReject(feedback="Only the app's own tools are available.")


def _fail(w: "CopilotJob", d: Any) -> None:
    st = int(d.status_code or 0)
    msg = str(d.message or "Copilot didn't answer.")
    kind = " ".join(str(x or "") for x in (d.error_type, d.error_code, d.provider_error_code)).lower()
    low = msg.lower()
    if st == 401 or "authentication" in kind:
        w.events.failed("auth", msg)
    elif st == 402 or any(x in kind or x in low for x in ("quota", "credit", "budget", "billing")):
        w.limit("copilot", None, msg)
    elif st == 429 or "rate" in kind or "rate limit" in low:
        w.limit("rate", None, msg)
    elif st == 403 or "policy" in low or "not enabled" in low:
        w.events.failed("denied", msg)
    elif st == 404 or ("model" in kind and "not" in low):
        w.events.failed("model_gone", msg)
    elif st >= 500 or "overload" in low:
        w.events.failed("busy", msg)
    elif any(x in low for x in ("fetch failed", "connect", "network", "getaddrinfo")):
        w.events.failed("ext_offline", msg)
    else:
        w.events.failed("error", msg)


# ------------------------------------------------------------------ the request thread
class CopilotJob(ExternalChat):
    """Like the other external chat jobs (same events), plus ``credits``: AI credits a model call
    used, and ``permission``: Copilot's web_fetch wants a page (url, reply(ok)) - the app asks.
    Instead of the trimmed context it gets the app's whole history (``req.history``): the runtime
    keeps the conversation (``req.conversation``) and only gets what is new (``CopilotChat.step``).
    The SDK's events are reported from the runtime's asyncio thread."""

    def __init__(self, req: ChatRequest, events: ChatEvents) -> None:
        super().__init__("copilot", req, events)
        self.messages = req.history          # (the whole history, as the app keeps it)
        self.chat: CopilotChat = req.conversation  # type: ignore[assignment]
        self.history, self.system, self.auth, self.info = req.history, req.system, req.auth, req.info or {}

    def cancel(self) -> None:
        super().cancel()
        _LOOP.submit(self.chat.abort())

    def run(self) -> None:
        try:
            token = token_of(self.creds, self.auth)
        except CopilotError as e:
            self.events.failed(e.kind, str(e))
            return
        fut = _LOOP.submit(self.chat.step(self, token))
        while True:
            try:
                fut.result(0.25)
                return
            except concurrent.futures.TimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001
                if not self._cancel:
                    err = _error_of(exc)
                    if err.kind == "error":
                        log.error("Copilot request failed", exc_info=exc)
                    self.events.failed(err.kind, str(err))
                return
