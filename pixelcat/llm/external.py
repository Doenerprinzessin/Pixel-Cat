"""External AI models: Anthropic (Claude) and OpenAI (ChatGPT).

* Model tags: ``anthropic:<model id>`` and ``openai:<model id>`` - used like the local
  ``qwen3.5:9b`` tags (the default model, or one chat's model).
* Sign-in or API key per provider.  Keys and sign-in tokens live in ``keys.dat``, encrypted
  by Windows for this account (like the save) - never in config.json, never in the log, and
  only ever sent to the provider itself.
* Token limits (external models only - local ones have none): a limit per provider that
  resets every day / week / month.  When it is reached the chat suggests switching model or
  raising it for now (until the reset); raises go up to a maximum - "all tokens" - and then
  a popup says when it resets.  The provider's own limits (rate limits, credits, the ChatGPT
  plan) end up in the same popup.
* Thinking on/off and effort, for external models.

The requests themselves: anthropic_client.py (the official SDK) and openai_client.py
(the Responses API, with an API key or "Sign in with ChatGPT").
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from .. import paths, savecrypt

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    key: str
    name: str            # the company
    brand: str           # the models
    signin: str          # the sign-in button
    key_url: str         # where an API key is made
    key_hint: str        # what a key starts with
    usage_url: str       # where the account's usage / credits are


ANTHROPIC = Provider("anthropic", "Anthropic", "Claude", "Sign in with Anthropic",
                     "https://platform.claude.com/settings/keys", "sk-ant-", "https://platform.claude.com/")
OPENAI = Provider("openai", "OpenAI", "ChatGPT", "Sign in with ChatGPT",
                  "https://platform.openai.com/api-keys", "sk-", "https://chatgpt.com/settings/usage")
PROVIDERS: dict[str, Provider] = {p.key: p for p in (ANTHROPIC, OPENAI)}

LIMITS = [0, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000]
PERIODS = {"day": "Every day", "week": "Every week", "month": "Every month"}
PERIOD_WORD = {"day": "today", "week": "this week", "month": "this month"}
ANT_DOWNLOAD = "https://github.com/anthropics/anthropic-cli/releases"
BILLING = {"anthropic": "https://platform.claude.com/settings/billing",
           "openai": "https://platform.openai.com/settings/organization/billing/overview"}
MAXIMUMS = [1.5, 2.0, 3.0, 5.0, 0.0]      # how far "raise for now" may go (x the limit; 0: no maximum)
EFFORTS = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max"}
DEFAULTS = {"limit": 1_000_000, "period": "day", "maximum": 2.0}
BASE_URLS: dict[str, str | None] = {"anthropic": None, "openai": "https://api.openai.com/v1"}   # (tests)


def parse(tag: str | None) -> tuple[str, str] | None:
    """``anthropic:claude-opus-5-5`` -> ("anthropic", "claude-opus-5-5"); a local tag -> None."""
    if not tag:
        return None
    prov, sep, rest = tag.partition(":")
    return (prov, rest) if sep and prov in PROVIDERS and rest else None


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def fmt_reset(at: datetime, now: datetime | None = None) -> str:
    """'today at 18:00 (in 2 h 5 min)', 'tomorrow at 00:00 (...)', 'Mon 24 Nov at 00:00 (...)'."""
    now = now or datetime.now()
    day = (at.date() - now.date()).days
    when = "today" if day == 0 else "tomorrow" if day == 1 else at.strftime("%a %d %b")
    left = max(0, int((at - now).total_seconds()))
    d, rest = divmod(left, 86400)
    h, m = divmod(rest // 60, 60)
    span = (f"{d} d {h} h" if d else f"{h} h {m} min" if h else f"{max(1, m)} min")
    return f"{when} at {at:%H:%M} (in {span})"


# ------------------------------------------------------------------ keys and sign-ins
class Credentials:
    """API keys and sign-in tokens, encrypted for this Windows account (keys.dat)."""

    MAGIC = b"PXCATK1\n"

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder
        self._data: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def _path(self) -> Path:
        return (self.folder or paths.data_dir()) / "keys.dat"

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = {}
            try:
                raw = self._path().read_bytes()
                if raw.startswith(self.MAGIC):
                    data = json.loads(savecrypt.unprotect(raw[len(self.MAGIC):]).decode("utf-8"))
                    self._data = data if isinstance(data, dict) else {}
            except FileNotFoundError:
                pass
            except (OSError, ValueError, savecrypt.Unreadable):
                log.warning("keys.dat can't be read (another account?) - external models need signing in again")
        return self._data

    def get(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return self._load().get(name, default)

    def set(self, name: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            if value is None:
                data.pop(name, None)
            else:
                data[name] = value
            blob = self.MAGIC + savecrypt.protect(json.dumps(data).encode("utf-8"))
            path = self._path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)


class MemoryCredentials(Credentials):
    """Credentials that are only tried, not kept (checking a new key)."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__()
        self._data = dict(data)

    def set(self, name: str, value: Any) -> None:
        assert self._data is not None
        if value is None:
            self._data.pop(name, None)
        else:
            self._data[name] = value


# ------------------------------------------------------------------ names and messages
NAMES: dict[str, str] = {}          # tag -> the provider's name for the model (filled from the model lists)


def label(tag: str) -> str:
    ext = parse(tag)
    if ext is None:
        return tag
    return f"{NAMES.get(tag) or ext[1]} ({PROVIDERS[ext[0]].name})"


def short_label(tag: str) -> str:
    """For the chat's model button: "OPUS 5.5", "GPT-6.1"."""
    ext = parse(tag)
    if ext is None:
        return tag[:6].upper()
    name = NAMES.get(tag) or ext[1]
    for word in ("Claude ", "claude-"):
        name = name.replace(word, "")
    spaced = " " in name
    parts = name.split() if spaced else name.replace("_", "-").split("-")
    out = parts[0]
    if len(parts) > 1 and any(ch.isdigit() for ch in parts[1]) and len(out) + len(parts[1]) < 10:
        out += (" " if spaced else "-") + parts[1]
    return out.upper()[:10]


def friendly(kind: str, detail: str, provider: str, model: str) -> str:
    prov = PROVIDERS.get(provider, ANTHROPIC)
    where = "Settings > AI model > External AI models"
    if kind == "auth":
        return f"Mew... {prov.name} didn't accept my sign-in ({detail[:160]}). Check it in {where}."
    if kind == "ext_offline":
        return f"I can't reach {prov.name}. Is the internet on? Try again in a moment."
    if kind == "timeout":
        return f"{prov.name} took too long to answer. Try again?"
    if kind == "refused":
        cat = f" ({detail})" if detail else ""
        return (f"The model said no to this one{cat}. Try saying it differently, or pick another model "
                "with the button next to SEND.")
    if kind == "truncated":
        return "My answer got too long and was cut off in the middle of a step. Try again, or ask for a smaller step."
    if kind == "model_gone":
        return f"{prov.name} doesn't know the model **{model}** (any more?). Pick another one in {where}."
    if kind == "denied":
        return f"{prov.name} says I'm not allowed to do that: {detail[:200]}"
    if kind == "busy":
        return f"{prov.name} is very busy right now. Try again in a moment."
    return f"Hiss... {prov.name} said:\n```text\n{detail[:300]}\n```"


# ------------------------------------------------------------------ token limits
def period_start(period: str, now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def next_reset(period: str, now: datetime) -> datetime:
    start = period_start(period, now)
    if period == "week":
        return start + timedelta(days=7)
    if period == "month":
        return (start + timedelta(days=32)).replace(day=1)
    return start + timedelta(days=1)


class Budget:
    """The token limit of one provider: ``settings`` (limit, period, maximum) and ``usage``
    (start of the period, tokens used, the limit with today's raises) - both plain dicts
    that live in the save."""

    def __init__(self, settings: dict[str, Any], usage: dict[str, Any]) -> None:
        self.s, self.u = settings, usage

    @property
    def limit(self) -> int:
        return int(self.s.get("limit", DEFAULTS["limit"]) or 0)

    @property
    def period(self) -> str:
        p = self.s.get("period", DEFAULTS["period"])
        return p if p in PERIODS else "day"

    @property
    def maximum(self) -> int:
        """All tokens: the limit with every raise (0: no maximum)."""
        mult = float(self.s.get("maximum", DEFAULTS["maximum"]) or 0)
        return int(self.limit * mult) if self.limit and mult else 0

    def _roll(self, now: datetime) -> None:
        start = period_start(self.period, now).timestamp()
        if self.u.get("start") != start:
            self.u.update(start=start, used=0, allowed=self.limit)

    def used(self, now: datetime | None = None) -> int:
        self._roll(now or datetime.now())
        return int(self.u.get("used", 0))

    def allowed(self, now: datetime | None = None) -> int:
        """The limit right now (raised for the rest of the period, perhaps)."""
        self._roll(now or datetime.now())
        return max(self.limit, int(self.u.get("allowed") or 0)) if self.limit else 0

    def status(self, now: datetime | None = None) -> str:
        """ok, soft (the limit is reached: switch or raise), hard (all tokens are used)."""
        now = now or datetime.now()
        if not self.limit:
            return "ok"
        used = self.used(now)
        if self.maximum and used >= self.maximum:
            return "hard"
        return "soft" if used >= self.allowed(now) else "ok"

    def next_allowed(self, now: datetime | None = None) -> int:
        """What 'raise for now' raises the limit to: half a limit more, up to the maximum."""
        nxt = max(self.allowed(now), self.used(now)) + max(1, self.limit // 2)
        return min(nxt, self.maximum) if self.maximum else nxt

    def raise_limit(self, now: datetime | None = None) -> int:
        self.u["allowed"] = self.next_allowed(now)
        return int(self.u["allowed"])

    def add(self, tokens: int, now: datetime | None = None) -> None:
        self._roll(now or datetime.now())
        self.u["used"] = int(self.u.get("used", 0)) + max(0, int(tokens))

    def reset_at(self, now: datetime | None = None) -> datetime:
        return next_reset(self.period, now or datetime.now())


# ------------------------------------------------------------------ the request thread
class ExternalWorker(QThread):
    """Like ollama_client.ChatWorker (same signals, same messages in), for an external model.
    ``failed`` kinds: auth, ext_offline, timeout, limit (detail: JSON with why/reset/url),
    refused, truncated, model_gone, denied, busy, error.  ``used``: tokens this request used.
    ``raw``: the model's reply as the provider's own blocks (replayed unchanged next time)."""

    token = Signal(str)
    thinking = Signal(int)
    done = Signal(str, list)
    failed = Signal(str, str)
    used = Signal(str, int)

    def __init__(self, provider: str, model: str, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | None, think: bool, effort: str, creds: Credentials) -> None:
        super().__init__()
        self.provider, self.model = provider, model
        self.messages, self.tools = messages, tools
        self.think, self.effort = think, effort
        self.creds = creds
        self.raw: list[dict[str, Any]] | None = None
        self.note = ""                       # something to tell the user once (e.g. "this model always thinks")
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def limit(self, why: str, reset: datetime | None = None, detail: str = "") -> None:
        self.failed.emit("limit", json.dumps({"provider": self.provider, "why": why, "detail": detail[:300],
                                              "reset": reset.timestamp() if reset else None}))


def tool_call_ids(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool call gets an id (the model's own, or a made-up one) and every tool result
    the id of its call: ``_id`` on the calls, ``_call_id`` on the results (None: a result
    without a call).  Returns copies."""
    out: list[dict[str, Any]] = []
    pending: list[str] = []
    for i, m in enumerate(msgs):
        m = dict(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for j, tc in enumerate(m["tool_calls"]):
                tc = dict(tc)
                tc["_id"] = str(tc.get("id") or f"call_{i}_{j}")
                calls.append(tc)
            m["tool_calls"] = calls
            pending = [tc["_id"] for tc in calls]
        elif m.get("role") == "tool":
            m["_call_id"] = pending.pop(0) if pending else None
        elif m.get("role") == "user":
            m["_unanswered"] = pending      # calls that never got a result (stopped)
            pending = []
        out.append(m)
    if pending and out:
        out.append({"role": "user", "content": "", "_unanswered": pending})
    return out


def media_type(b64: str) -> str:
    head = b64[:12]
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lG"):
        return "image/gif"
    if head.startswith("UklG"):
        return "image/webp"
    return "image/jpeg"


def wait_reset(seconds: float | None) -> datetime | None:
    return datetime.now() + timedelta(seconds=seconds) if seconds and seconds > 0 else None


def now_ts() -> float:
    return time.time()
