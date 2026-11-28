"""Claude through Anthropic's official SDK (``anthropic``): models, sign-in, chat requests.

* Credentials: the API key from keys.dat, or Pixel Cat's own sign-in made with Anthropic's
  ``ant`` helper (ant_tool.py) - given to the SDK as its in-memory profile config; the SDK
  reads and renews the tokens itself.
* Requests stream (``messages.stream``) with the cat's tools, screenshots as images, adaptive
  thinking (summaries shown as "thinking...") and ``output_config.effort``.  Each model
  accepts a different thinking setting, so a refused one steps down (and is remembered):
  thinking off: disabled -> between_tools -> always on with low effort.
* The reply's own blocks (thinking with signatures, text, tool calls) are replayed unchanged
  in the next request of the same task.  Pixel Cat shortens old history, so requests say
  ``drop_block`` for thinking that no longer matches instead of failing.
* Server-side fallbacks ("default") for the models that have them: a refused request is
  re-run on a fallback model inside the same call.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import anthropic

from ..ports.llm import ChatEvents, ChatRequest
from .chat_job import ExternalChat
from .external import (ANTHROPIC, BASE_URLS, CLAUDE_MAX_OUT, SEARCHING, Credentials, is_too_long, media_type,
                       too_long, tool_call_ids, wait_reset, with_sources)

log = logging.getLogger(__name__)

MAX_OUT = CLAUDE_MAX_OUT
WEB_SEARCHES = 5                 # web searches Claude may run in one request (Anthropic's server tool)
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
BETA_BINDING = "thinking-binding-controls-2026-08-01"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]
_learned: dict[str, dict[str, Any]] = {}      # model -> what it refused (per run of the app)


def client(creds: Credentials, auth: str = "key", timeout: float = 600.0) -> anthropic.Anthropic:
    """With the saved API key (auth "key"), or Pixel Cat's own Anthropic sign-in ("signin")."""
    from . import ant_tool

    kw: dict[str, Any] = {"max_retries": 2, "timeout": timeout}
    if BASE_URLS.get("anthropic"):
        kw["base_url"] = BASE_URLS["anthropic"]
    if auth == "signin":
        cfg = ant_tool.client_config()
        if cfg is None:
            raise anthropic.CredentialsError("Not signed in with Anthropic any more - sign in again.")
        return anthropic.Anthropic(config=cfg, **kw)
    key = creds.get("anthropic_key")
    if not key:
        raise anthropic.CredentialsError("No Anthropic API key saved.")
    return anthropic.Anthropic(api_key=key, **kw)


def _cap(caps: Any, *path: str) -> bool:
    cur = caps
    try:
        for p in path:
            cur = cur[p] if isinstance(cur, dict) else getattr(cur, p)
        return bool(cur["supported"] if isinstance(cur, dict) else getattr(cur, "supported"))
    except (KeyError, AttributeError, TypeError):
        return False


def list_models(creds: Credentials, auth: str = "key") -> list[dict[str, Any]]:
    """The account's models (newest first) with what each supports.  Raises the SDK's errors."""
    out = []
    for m in client(creds, auth, 30.0).models.list(limit=100):
        caps = getattr(m, "capabilities", None) or {}
        out.append({"id": m.id, "name": getattr(m, "display_name", None) or m.id,
                    "adaptive": _cap(caps, "thinking", "types", "adaptive"),
                    "enabled": _cap(caps, "thinking", "types", "enabled"),
                    "effort": [e for e in EFFORT_ORDER if _cap(caps, "effort", e)],
                    "vision": _cap(caps, "image_input") if caps else True,
                    "max_tokens": int(getattr(m, "max_tokens", 0) or 0),
                    "context": int(getattr(m, "max_input_tokens", 0) or 0),
                    "web_search": _cap(caps, "server_tools", "web_search") if caps else True})
    return out


# ------------------------------------------------------------------ the cat's messages -> Claude's
def _images(m: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "image", "source": {"type": "base64", "media_type": media_type(b), "data": b}}
            for b in m.get("images") or [] if isinstance(b, str) and b]


def _clean_block(b: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in b.items() if v is not None}


def to_claude(msgs: list[dict[str, Any]], strip_thinking: bool = False) -> tuple[str, list[dict[str, Any]]]:
    """(system, messages): tool calls paired with their results by id, results first in their
    user turn, screenshots as image blocks, the model's own blocks replayed when there are."""
    system = ""
    out: list[dict[str, Any]] = []

    def user(blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == "user":
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": "user", "content": blocks})

    for m in tool_call_ids(msgs):
        role = m.get("role")
        text = str(m.get("content") or "")
        if role == "system":
            if not system:
                system = text
            else:
                user([{"type": "text", "text": text}])
            continue
        if role == "user":
            blocks = [{"type": "tool_result", "tool_use_id": cid, "content": "(not run - the user stopped it)",
                       "is_error": True} for cid in m.get("_unanswered") or []]
            blocks += _images(m)
            if text.strip():
                blocks.append({"type": "text", "text": text})
            user(blocks)
        elif role == "tool":
            body: list[dict[str, Any]] = [{"type": "text", "text": text or "(done)"}] + _images(m)
            if m.get("_call_id"):
                user([{"type": "tool_result", "tool_use_id": m["_call_id"], "content": body,
                       "is_error": text.startswith("error")}])
            else:
                user([{"type": "text", "text": f"[result of {m.get('tool_name', 'a tool')}] {text}"}] + _images(m))
        elif role == "assistant":
            raw = m.get("raw")
            calls = m.get("tool_calls") or []
            if isinstance(raw, list) and raw:
                blocks = [_clean_block(b) for b in raw if isinstance(b, dict)]
                if strip_thinking:
                    blocks = [b for b in blocks if b.get("type") not in ("thinking", "redacted_thinking")]
                ids = [b.get("id") for b in blocks if b.get("type") == "tool_use"]
                if len(ids) != len(calls) or not all(ids):
                    blocks = []                  # doesn't match the history: rebuild it below
            else:
                blocks = []
            if not blocks:
                if text.strip():
                    blocks.append({"type": "text", "text": text})
                for tc in calls:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {}
                    blocks.append({"type": "tool_use", "id": tc["_id"], "name": fn.get("name", ""), "input": args})
            elif calls:                          # the tool calls' ids come from the replayed blocks
                for tc, b in zip(calls, [b for b in blocks if b.get("type") == "tool_use"]):
                    tc["_id"] = b["id"]
            if not blocks:
                blocks = [{"type": "text", "text": "(done)"}]
            out.append({"role": "assistant", "content": blocks})
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continuing)"}]})
    _fix_result_ids(out)
    return system, out


def _fix_result_ids(out: list[dict[str, Any]]) -> None:
    """Results answer the ids of the assistant turn right before them (raw blocks may carry
    the model's own ids)."""
    for i in range(1, len(out)):
        if out[i]["role"] != "user" or out[i - 1]["role"] != "assistant":
            continue
        ids = [b["id"] for b in out[i - 1]["content"] if b.get("type") == "tool_use"]
        results = [b for b in out[i]["content"] if b.get("type") == "tool_result"]
        for b, cid in zip(results, ids):
            b["tool_use_id"] = cid
        answered = {b["tool_use_id"] for b in results}
        missing = [{"type": "tool_result", "tool_use_id": cid, "content": "(not run)", "is_error": True}
                   for cid in ids if cid not in answered]
        others = [b for b in out[i]["content"] if b.get("type") != "tool_result"]
        out[i]["content"] = results + missing + others
    last = out[-1] if out else None
    if last is not None and last["role"] == "assistant" and any(b.get("type") == "tool_use" for b in last["content"]):
        out.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": "(not run)",
                                                 "is_error": True}
                                                for b in last["content"] if b.get("type") == "tool_use"]})


def tools_for_claude(tools: list[dict[str, Any]] | None, eager: bool = True) -> list[dict[str, Any]]:
    out = []
    for t in tools or []:
        fn = t.get("function") or t
        spec = {"name": fn["name"], "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}
        if eager:
            spec["eager_input_streaming"] = True
        out.append(spec)
    return out


# ------------------------------------------------------------------ the request
class AnthropicChat(ExternalChat):
    def __init__(self, req: ChatRequest, events: ChatEvents) -> None:
        super().__init__("anthropic", req, events)
        self.info = req.info or {}           # the model's capabilities (from the model list)
        self.auth = req.auth
        self._cont: list[dict[str, Any]] = []   # a reply paused by the server (long web searches), continued
        self._pauses = 0

    # what to send ---------------------------------------------------------------
    def _effort(self, opts: dict[str, Any]) -> str | None:
        levels = self.info.get("effort")
        if opts.get("no_effort") or levels == []:
            return None
        want = opts.get("effort") or self.effort
        if not self.think and want in ("xhigh", "max"):
            want = "high"                    # thinking can't be off above high
        if levels:
            ok = [e for e in EFFORT_ORDER[:EFFORT_ORDER.index(want) + 1] if e in levels] if want in EFFORT_ORDER else []
            want = ok[-1] if ok else levels[0]
        return want

    def _params(self, opts: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        system, msgs = to_claude(self.messages, opts.get("strip_thinking", False))
        if self._cont:
            msgs = msgs + [{"role": "assistant", "content": self._cont}]
        p: dict[str, Any] = {"model": self.model, "max_tokens": min(MAX_OUT, self.info.get("max_tokens") or MAX_OUT),
                             "messages": msgs}
        if system:
            p["system"] = [{"type": "text", "text": system}]
        tools = tools_for_claude(self.tools, not opts.get("no_eager")) if self.tools else []
        if self.web and not opts.get("no_search") and self.info.get("web_search", True):
            tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_SEARCHES})
        if tools:
            p["tools"] = tools
        if not opts.get("no_cache"):
            p["cache_control"] = {"type": "ephemeral"}
        betas: list[str] = []
        mode = opts.get("thinking") or ("adaptive" if self.think else "disabled")
        if mode == "adaptive":
            p["thinking"] = {"type": "adaptive", "display": "summarized"}
            if not opts.get("no_binding"):
                p["thinking"]["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
                betas.append(BETA_BINDING)
        elif mode == "enabled":
            p["thinking"] = {"type": "enabled", "budget_tokens": 4096}
        elif mode in ("disabled", "between_tools"):
            p["thinking"] = {"type": mode}
        effort = "low" if mode == "always" else self._effort(opts)
        if effort and mode != "enabled":
            p["output_config"] = {"effort": effort}
        if self.model in FALLBACK_MODELS and not opts.get("no_fallback"):
            p["fallbacks"] = "default"
            betas.append(BETA_FALLBACK)
        return p, betas

    def _first_opts(self) -> dict[str, Any]:
        opts = dict(_learned.get(self.model, {}))
        if self.think:
            if "thinking_on" in opts:
                opts["thinking"] = opts.pop("thinking_on")
            elif self.info and not self.info.get("adaptive"):
                opts["thinking"] = "enabled" if self.info.get("enabled") else "none"
        else:
            if "thinking_off" in opts:
                opts["thinking"] = opts.pop("thinking_off")
        opts.pop("thinking_on", None)
        opts.pop("thinking_off", None)
        return opts

    def _step_down(self, opts: dict[str, Any], msg: str) -> bool:
        """A 400 named something this model doesn't take: change it and try again (and
        remember it).  False: nothing left to change."""
        low = msg.lower()
        learn = _learned.setdefault(self.model, {})
        if ("web_search" in low or "web search" in low) and not opts.get("no_search"):
            opts["no_search"] = learn["no_search"] = True
            if "not enabled" in low or "disabled" in low:
                self.note = ("Web search is turned off for your Anthropic organization (Claude Console > Settings > "
                             "Capabilities), so I answer without it.")
        elif "fallback" in low and not opts.get("no_fallback"):
            opts["no_fallback"] = learn["no_fallback"] = True
        elif ("block_binding" in low or "thinking-binding" in low) and not opts.get("no_binding"):
            opts["no_binding"] = learn["no_binding"] = True
        elif "signature" in low or "bound to a different conversation" in low or "thinking block" in low \
                or "redacted_thinking" in low:
            if opts.get("strip_thinking"):
                return False
            opts["strip_thinking"] = True          # (this request only)
        elif "eager_input_streaming" in low and not opts.get("no_eager"):
            opts["no_eager"] = learn["no_eager"] = True
        elif "cache_control" in low and not opts.get("no_cache"):
            opts["no_cache"] = learn["no_cache"] = True
        elif "thinking" in low or "between_tools" in low or "budget_tokens" in low:
            cur = opts.get("thinking") or ("adaptive" if self.think else "disabled")
            nxt = ({"disabled": "between_tools", "between_tools": "always"} if not self.think
                   else {"adaptive": "enabled", "enabled": "none"}).get(cur)
            if nxt is None:
                return False
            opts["thinking"] = nxt
            learn["thinking_off" if not self.think else "thinking_on"] = nxt
            if nxt == "always":
                self.note = ("This model always thinks, so with Thinking off I use low effort instead "
                             "(Settings > AI model > External AI models).")
        elif ("effort" in low or "output_config" in low) and not opts.get("no_effort"):
            opts["no_effort"] = learn["no_effort"] = True
        else:
            return False
        log.info("%s refused a setting - trying again with %s", self.model,
                 {k: v for k, v in opts.items() if k != "strip_thinking"})
        return True

    # the call -----------------------------------------------------------------------
    def run(self) -> None:  # noqa: C901 - one linear request with its error cases
        opts = self._first_opts()
        if opts.get("thinking") == "always":
            self.note = ("This model always thinks, so with Thinking off I use low effort instead "
                         "(Settings > AI model > External AI models).")
        tries = 0
        while tries < 8 and not self._cancel:
            tries += 1
            params, betas = self._params(opts)
            try:
                c = client(self.creds, self.auth)
                thought = 0
                stream_ctx = (c.beta.messages.stream(**params, betas=betas) if betas
                              else c.beta.messages.stream(**params))
                with stream_ctx as stream:
                    for event in stream:
                        if self._cancel:
                            return
                        if event.type == "content_block_delta":
                            d = event.delta
                            if d.type == "text_delta" and d.text:
                                self.events.token(d.text)
                            elif d.type == "thinking_delta":
                                thought += len(d.thinking or "") or 1
                                self.events.thinking(thought)
                        elif event.type == "content_block_start" and getattr(event.content_block, "type", "") == "thinking":
                            self.events.thinking(max(1, thought))
                        elif event.type == "content_block_start" and \
                                getattr(event.content_block, "type", "") == "server_tool_use":
                            self.events.status(SEARCHING)
                    final = stream.get_final_message()
            except ValueError as exc:            # a tool call's JSON the SDK couldn't read: ask again
                log.warning("Claude's tool call couldn't be read (%s) - asking again", exc)
                continue
            except anthropic.BadRequestError as exc:
                msg = _message(exc)
                if is_too_long(msg):
                    self.events.failed("too_long", too_long(msg))
                    return
                if "credit balance" in msg.lower():
                    self.limit("credits", None, msg)
                    return
                if self._step_down(opts, msg):
                    continue
                self.events.failed("error", msg)
                return
            except anthropic.AuthenticationError as exc:
                self.events.failed("auth", _message(exc))
                return
            except anthropic.PermissionDeniedError as exc:
                self.events.failed("denied", _message(exc))
                return
            except anthropic.NotFoundError as exc:
                self.events.failed("model_gone", _message(exc))
                return
            except anthropic.RateLimitError as exc:
                msg = _message(exc)
                why = "spend" if "spend" in type(exc).__name__.lower() or "spend limit" in msg.lower() else "rate"
                self.limit(why, None if why == "spend" else _reset_from(exc), msg)
                return
            except anthropic.PaymentRequiredError as exc:
                self.limit("credits", None, _message(exc))
                return
            except anthropic.InternalServerError as exc:
                self.events.failed("busy", _message(exc))
                return
            except anthropic.APITimeoutError:
                self.events.failed("timeout", "")
                return
            except anthropic.APIConnectionError as exc:
                if not self._cancel:
                    self.events.failed("ext_offline", str(exc))
                return
            except anthropic.APIStatusError as exc:
                self.events.failed("error", f"HTTP {exc.status_code}: {_message(exc)}")
                return
            except anthropic.AnthropicError as exc:      # no credentials found, and the like
                self.events.failed("auth", str(exc))
                return
            except Exception as exc:  # noqa: BLE001
                if not self._cancel:
                    log.exception("Claude request failed")
                    self.events.failed("error", str(exc))
                return
            if getattr(final, "stop_reason", None) == "pause_turn" and self._pauses < 4:
                self._pauses += 1                    # (a long search paused the reply: send it back to go on)
                tries -= 1
                self._usage(final)
                self._cont += [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in final.content]
                continue
            self._finish(final)
            return

    def _usage(self, final: Any) -> None:
        u = final.usage
        total = sum(int(getattr(u, k, 0) or 0) for k in
                    ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.events.used("anthropic", total)
        searches = int(getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0)
        if searches:
            log.info("%s: %d web search(es)", self.model, searches)

    def _finish(self, final: Any) -> None:
        self._usage(final)
        blocks = self._cont + [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in final.content]
        # after a server-side fallback: what came before the last fallback block was the
        # declined attempt - its thinking and tool calls are neither run nor replayed
        cut = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
        keep = [b for i, b in enumerate(blocks)
                if i > cut or b.get("type") not in ("thinking", "redacted_thinking", "tool_use", "server_tool_use")]
        text = "".join(b.get("text", "") for b in keep if b.get("type") == "text")
        sources = [(str(c.get("title") or ""), str(c.get("url") or "")) for b in keep if b.get("type") == "text"
                   for c in b.get("citations") or [] if isinstance(c, dict) and c.get("url")]
        text = with_sources(text, sources)
        stop = getattr(final, "stop_reason", None)
        if stop == "refusal":
            det = getattr(final, "stop_details", None)
            self.events.failed("refused", str(getattr(det, "category", "") or ""))
            return
        calls = []
        for b in keep:
            if b.get("type") == "tool_use":
                if not isinstance(b.get("input"), dict):
                    b["input"] = {}
                calls.append({"id": b.get("id"), "name": b.get("name", ""), "arguments": b["input"]})
        if stop == "max_tokens" and calls:
            self.events.failed("truncated", "")
            return
        self.raw = keep
        self.events.done(text, calls)


def _message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(getattr(exc, "message", "") or exc)


def _reset_from(exc: Exception) -> datetime | None:
    """When a rate limit lifts: the earliest of Anthropic's reset headers, or retry-after."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    empty, other = [], []
    for kind in ("tokens", "input-tokens", "output-tokens", "requests"):
        val = headers.get(f"anthropic-ratelimit-{kind}-reset")
        if not val:
            continue
        try:
            at = datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except ValueError:
            continue
        if at <= datetime.now():
            continue
        left = headers.get(f"anthropic-ratelimit-{kind}-remaining")
        (empty if left is not None and str(left).strip() == "0" else other).append(at)
    if empty:                                    # the bucket that ran out
        return max(empty)
    try:
        after = wait_reset(float(headers.get("retry-after") or 0))
    except ValueError:
        after = None
    return after or (max(other) if other else None)


def signin_status(creds: Credentials) -> bool:
    """Is Pixel Cat's own Anthropic sign-in there, and does the API accept it?"""
    try:
        client(creds, "signin", 15.0).with_options(max_retries=0).models.list(limit=1)
        return True
    except Exception:  # noqa: BLE001 - not signed in (or offline)
        return False


__all__ = ["AnthropicChat", "list_models", "signin_status", "to_claude", "client", "ANTHROPIC", "json"]
