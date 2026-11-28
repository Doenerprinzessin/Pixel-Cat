"""External AI models: Anthropic (Claude), OpenAI (ChatGPT) and GitHub Copilot.

* Model tags: ``anthropic:<model id>``, ``openai:<model id>`` and ``copilot:<model id>`` -
  used like the local ``qwen3.5:9b`` tags (the default model, or one chat's model).
* Sign-in or API key (GitHub: a token) per provider.  Keys and sign-in tokens live in
  ``keys.dat``, encrypted by Windows for this account (like the save) - never in config.json,
  never in the log, and only ever sent to the provider itself.
* Token limits (external models only - local ones have none): a limit per provider that
  resets every day / week / month.  When it is reached the chat suggests switching model or
  raising it for now (until the reset); raises go up to a maximum - "all tokens" - and then
  a popup says when it resets.  The provider's own limits (rate limits, credits, the ChatGPT
  plan, the Copilot allowance) end up in the same popup.
* Copilot also has a credit limit: how many of the Copilot plan's AI credits the cat may use
  in a month (``CreditBudget``).  When it is reached the chat offers to raise it for now.
* Thinking on/off and effort, for external models.

The requests themselves: anthropic_client.py (the official SDK), openai_client.py (the
Responses API, with an API key or "Sign in with ChatGPT") and copilot_client.py (GitHub's
official Copilot SDK).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .. import paths, savecrypt

if TYPE_CHECKING:
    from ..ports import SecretStore

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
COPILOT = Provider("copilot", "GitHub", "Copilot", "Sign in with GitHub",
                   "https://github.com/settings/personal-access-tokens/new", "github_pat_",
                   "https://github.com/settings/copilot")
PROVIDERS: dict[str, Provider] = {p.key: p for p in (ANTHROPIC, OPENAI, COPILOT)}

LIMITS = [0, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000,
          50_000_000, 100_000_000]
MIN_LIMIT, MAX_LIMIT = 1_000, 100_000_000          # a token limit typed in (Custom...)
PERIODS = {"day": "Every day", "week": "Every week", "month": "Every month"}
PERIOD_WORD = {"day": "today", "week": "this week", "month": "this month"}
BILLING = {"anthropic": "https://platform.claude.com/settings/billing",
           "openai": "https://platform.openai.com/settings/organization/billing/overview",
           "copilot": "https://github.com/settings/billing"}
MAXIMUMS = [1.5, 2.0, 3.0, 5.0, 0.0]      # how far "raise for now" may go (x the limit; 0: no maximum)
EFFORTS = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max"}
DEFAULTS = {"limit": 1_000_000, "period": "day", "maximum": 2.0}
CREDIT_LIMITS = [0, 500, 1000, 2000, 5000, 10_000, 20_000]
CREDIT_DEFAULT = 5000
BASE_URLS: dict[str, str | None] = {"anthropic": None, "openai": "https://api.openai.com/v1"}   # (tests)

# How much a request may hold (local models: num_ctx; Claude and ChatGPT: the model's own maximum).
CLAUDE_MAX_OUT = 32_000              # the longest Claude reply asked for (max_tokens) - its window holds it too
CLAUDE_WINDOW = 200_000              # a Claude model whose window the list doesn't say (Anthropic: "other models: 200k")
# OpenAI models' maximum input in tokens, from developers.openai.com/api/docs/models/<id> ("Maximum input tokens",
# else the context window minus the max output) - the API's model list doesn't say.  The longest matching name wins
# (dated snapshots like gpt-6.2-sol-2026-06-01 too).
OPENAI_INPUT = {
    "gpt-6.2": 922_000, "gpt-6.1": 922_000, "gpt-6": 922_000, "gpt-5.6": 922_000, "gpt-5.6-cyber": 272_000,
    "gpt-5.5": 922_000, "gpt-5.4": 922_000, "gpt-5.4-mini": 272_000, "gpt-5.4-nano": 272_000,
    "gpt-5.3": 272_000, "gpt-5.2": 272_000, "gpt-5.1": 272_000, "gpt-5": 272_000, "gpt-5-pro": 128_000,
    "gpt-5-chat": 111_616, "gpt-5.1-chat": 111_616, "gpt-5.2-chat": 111_616, "gpt-5.3-chat": 111_616,
    "chat-latest": 272_000, "gpt-4.1": 1_014_808, "gpt-4o": 111_616, "chatgpt-4o": 111_616, "gpt-4.5": 111_616,
    "gpt-4-turbo": 123_904, "o4-mini": 100_000, "o3": 100_000, "o1": 100_000, "o1-mini": 62_464,
    "o1-preview": 95_232,
}
OPENAI_UNKNOWN = 272_000             # a model that isn't listed above (a "too long" answer makes it smaller)
MIN_BUDGET = 16_000


def parse(tag: str | None) -> tuple[str, str] | None:
    """``anthropic:claude-opus-5-5`` -> ("anthropic", "claude-opus-5-5"); a local tag -> None."""
    if not tag:
        return None
    prov, sep, rest = tag.partition(":")
    return (prov, rest) if sep and prov in PROVIDERS and rest else None


# ------------------------------------------------------------------ context windows
def openai_input(model: str) -> int | None:
    """An OpenAI model's documented maximum input (None: not listed)."""
    best: tuple[str, int] | None = None
    for key, n in OPENAI_INPUT.items():
        if (model == key or model.startswith(key + "-")) and (best is None or len(key) > len(best[0])):
            best = (key, n)
    return best[1] if best else None


def max_input(provider: str, model: str, info: dict[str, Any]) -> int:
    """The most one request to this model may hold, in tokens: its own context window without the
    room for the reply.  ``info`` is the model list's entry (``context``: the window it reported)."""
    reported = int(info.get("context") or 0)
    if provider == "anthropic":
        out = min(CLAUDE_MAX_OUT, int(info.get("max_tokens") or CLAUDE_MAX_OUT))
        return max(MIN_BUDGET, (reported or CLAUDE_WINDOW) - out)
    known = openai_input(model)
    if reported:                             # (ChatGPT's own list: what the plan's model takes, reply included)
        cap = reported - min(128_000, reported // 4)
        return max(MIN_BUDGET, min(known, cap) if known else cap)
    return known or OPENAI_UNKNOWN


_TOO_LONG = ("prompt is too long", "exceed context limit", "context_length_exceeded", "exceeds the context window",
             "maximum context length", "context window of this model")


def is_too_long(message: str) -> bool:
    low = message.lower()
    return any(w in low for w in _TOO_LONG)


def too_long(message: str) -> str:
    """A 'request too long' error for the app: JSON with ``limit`` and ``actual`` input tokens
    (None where the provider didn't say)."""
    def num(s: str) -> int:
        return int(s.replace(",", ""))
    limit = actual = None
    if m := re.search(r"(\d[\d,]*)\s*\+\s*(\d[\d,]*)\s*>\s*(\d[\d,]*)", message):      # input + max_tokens > window
        actual, limit = num(m[1]), num(m[3]) - num(m[2])
    elif m := re.search(r"(\d[\d,]*)\s*tokens?\s*>\s*(\d[\d,]*)", message):            # 1012345 tokens > 1000000
        actual, limit = num(m[1]), num(m[2])
    else:
        if m := re.search(r"maximum context length is (\d[\d,]*)", message, re.I):
            limit = num(m[1])
        if m := re.search(r"(?:resulted in|requested|you sent) (\d[\d,]*)", message, re.I):
            actual = num(m[1])
    return json.dumps({"limit": limit, "actual": actual, "detail": message[:200]})


def fmt_tokens(n: int) -> str:
    """'750', '1.5k', '12k', '3.5M', '1.23M', '100M'."""
    if n >= 999_500:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def parse_amount(text: str) -> float | None:
    """A typed amount: '3.5M', '750k', '2 000 000', "2'000'000", '2,000,000', '3,5M', '100 million'
    (None: not a positive number)."""
    s = text.strip().lower()
    for ch in (" ", "\u00a0", "\u202f", "'", "\u2019", "_"):
        s = s.replace(ch, "")
    for word in ("tokens", "token", "credits", "credit"):
        s = s.removesuffix(word)
    mult = 1.0
    for suffix, m in (("million", 1e6), ("mio", 1e6), ("m", 1e6), ("thousand", 1e3), ("k", 1e3)):
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], m
            break
    if "," in s and "." in s:                     # the last one is the decimal point
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:                                # 2,000,000 (thousands) or 3,5 (decimal)
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif s.count(".") > 1:                        # 2.000.000
        s = s.replace(".", "")
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def fmt_credits(x: float) -> str:
    """AI credits like GitHub shows them: '0.04', '28.1', '5,000'."""
    x = max(0.0, float(x))
    if x >= 100:
        return f"{round(x):,}"
    s = f"{x:.2f}" if x < 1 else f"{x:.1f}"
    return s.rstrip("0").rstrip(".")


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

    def __init__(self, folder: Path | None = None, *, secrets: SecretStore) -> None:
        self.folder = folder
        self.secrets = secrets
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
                    data = json.loads(self.secrets.unprotect(raw[len(self.MAGIC):]).decode("utf-8"))
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
            blob = self.MAGIC + self.secrets.protect(json.dumps(data).encode("utf-8"))
            path = self._path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)


class MemoryCredentials(Credentials):
    """Credentials that are only tried, not kept (checking a new key)."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(secrets=None)  # type: ignore[arg-type] - never read from or written to keys.dat
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
    if kind == "install":
        return f"Mew... {detail[:200]} Connect {prov.brand} again in {where}."
    if kind == "too_long":
        name = NAMES.get(f"{provider}:{model}") or model
        return (f"Our chat got longer than **{name}** can take, even shortened. Press ＋ (or Ctrl+N) for a fresh "
                "chat 🐾")
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


class CreditBudget:
    """How many of the Copilot plan's AI credits the cat may use in a month: ``settings``
    (``credit_limit``) and ``usage`` (start of the month, credits used, the limit with this
    month's raises).  Reaching it only asks - raising lasts until the month ends, like
    GitHub's own allowance (which resets on the 1st)."""

    period = "month"

    def __init__(self, settings: dict[str, Any], usage: dict[str, Any]) -> None:
        self.s, self.u = settings, usage

    @property
    def limit(self) -> float:
        return float(self.s.get("credit_limit", CREDIT_DEFAULT) or 0)

    def _roll(self, now: datetime) -> None:
        start = period_start(self.period, now).timestamp()
        if self.u.get("start") != start:
            self.u.update(start=start, used=0.0, allowed=self.limit)

    def used(self, now: datetime | None = None) -> float:
        self._roll(now or datetime.now())
        return float(self.u.get("used", 0.0))

    def allowed(self, now: datetime | None = None) -> float:
        self._roll(now or datetime.now())
        return max(self.limit, float(self.u.get("allowed") or 0)) if self.limit else 0.0

    def status(self, now: datetime | None = None) -> str:
        """ok, or soft (the limit is reached: switch or raise for now)."""
        now = now or datetime.now()
        if not self.limit:
            return "ok"
        return "soft" if self.used(now) >= self.allowed(now) else "ok"

    def next_allowed(self, now: datetime | None = None) -> float:
        """'Raise for now': half a limit more than what is used (or allowed) already."""
        return max(self.allowed(now), self.used(now)) + max(1.0, self.limit / 2)

    def raise_limit(self, now: datetime | None = None) -> float:
        self.u["allowed"] = self.next_allowed(now)
        return float(self.u["allowed"])

    def add(self, credits: float, now: datetime | None = None) -> None:
        self._roll(now or datetime.now())
        self.u["used"] = float(self.u.get("used", 0.0)) + max(0.0, float(credits))

    def reset_at(self, now: datetime | None = None) -> datetime:
        return next_reset(self.period, now or datetime.now())


SEARCHING = "🔎 searching the web…"


def with_sources(text: str, sources: list[tuple[str, str]], limit: int = 8) -> str:
    """The reply with the web pages it used as clickable links (the providers ask apps to show
    them) - the ones the text doesn't already link to."""
    seen: set[str] = set()
    links = []
    for title, url in sources:
        if not url.startswith(("http://", "https://")) or url in seen or url in text:
            continue
        seen.add(url)
        name = (title or urlparse(url).netloc or url).replace("[", "(").replace("]", ")").strip()[:80]
        links.append(f"[{name}]({url})")
    if not links:
        return text
    return f"{text.rstrip()}\n\n**Sources:** " + " · ".join(links[:limit])


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
