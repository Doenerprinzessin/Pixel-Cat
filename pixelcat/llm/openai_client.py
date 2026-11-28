"""OpenAI models through the Responses API (``POST /v1/responses``, streamed, ``store: false``),
with an API key or with "Sign in with ChatGPT" (the user's ChatGPT plan, chatgpt_signin.py).

* The cat's messages become Responses input items: messages, ``function_call`` and
  ``function_call_output`` (paired by call id), screenshots as ``input_image``.
* Thinking/effort: ``reasoning.effort`` - off = "none" (or "minimal" on models without it).
  Something a model doesn't take is named in ``error.param``: it is changed and asked again.
* Limits: the ChatGPT plan's usage limit (OpenAI doesn't say when it resets - the popup links
  to chatgpt.com/settings/usage), API credits, and rate limits (reset from the headers).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import requests

from . import chatgpt_signin
from ..ports.llm import ChatEvents, ChatRequest
from .chat_job import ExternalChat
from .external import (BASE_URLS, SEARCHING, Credentials, is_too_long, media_type, too_long, tool_call_ids,
                       wait_reset, with_sources)

log = logging.getLogger(__name__)

EFFORT_STEPS = {"max": ["xhigh", "high"], "xhigh": ["xhigh", "high"], "high": ["high"], "medium": ["medium"],
                "low": ["low"], "off": ["none", "minimal"]}
_learned: dict[str, dict[str, Any]] = {}
_SKIP = ("audio", "realtime", "transcribe", "tts", "image", "dall-e", "whisper", "embedding", "moderation",
         "search", "instruct", "babbage", "davinci", "similarity", "edit")


class OpenAIError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _base() -> str:
    return (BASE_URLS.get("openai") or "https://api.openai.com/v1").rstrip("/")


def headers(creds: Credentials, auth: str, force: bool = False) -> dict[str, str]:
    if auth == "chatgpt":
        try:
            token = chatgpt_signin.access_token(creds, force)
        except chatgpt_signin.SignInError as e:
            raise OpenAIError("auth", str(e)) from e
    else:
        token = creds.get("openai_key") or ""
        if not token:
            raise OpenAIError("auth", "No OpenAI API key saved.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_models(creds: Credentials, auth: str) -> list[dict[str, Any]]:
    """Models for this sign-in/key.  ChatGPT sign-in: the server's list (visibility "list",
    server order); API key: the chat models, newest first."""
    try:
        r = requests.get(_base() + "/models", headers=headers(creds, auth), timeout=30)
        if r.status_code == 401 and auth == "chatgpt":
            r = requests.get(_base() + "/models", headers=headers(creds, auth, True), timeout=30)
    except requests.RequestException as e:
        raise OpenAIError("ext_offline", str(e)) from e
    data = _json(r)
    if r.status_code >= 400:
        raise OpenAIError("auth" if r.status_code == 401 else "error", _err_text(data) or f"HTTP {r.status_code}")
    if isinstance(data.get("models"), list):
        return [{"id": m.get("slug") or m.get("id"), "name": m.get("display_name") or m.get("slug") or m.get("id"),
                 **({"context": int(m.get("max_context_window") or m.get("context_window"))}
                    if m.get("max_context_window") or m.get("context_window") else {})}
                for m in data["models"] if isinstance(m, dict) and m.get("visibility", "list") == "list"
                and (m.get("slug") or m.get("id"))]
    rows = [m for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]
    rows = [m for m in rows if re.match(r"^(gpt-|o\d|chatgpt-|codex)", m["id"])
            and not any(s in m["id"] for s in _SKIP)]
    rows.sort(key=lambda m: -int(m.get("created") or 0))
    return [{"id": m["id"], "name": m["id"]} for m in rows]


# ------------------------------------------------------------------ the cat's messages -> Responses input
def _image_parts(m: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "input_image", "image_url": f"data:{media_type(b)};base64,{b}"}
            for b in m.get("images") or [] if isinstance(b, str) and b]


def to_responses(msgs: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions = ""
    items: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []         # screenshots from tool results, after the results

    def flush() -> None:
        items.extend(later)
        later.clear()

    for m in tool_call_ids(msgs):
        role = m.get("role")
        text = str(m.get("content") or "")
        if role != "tool":
            flush()
        if role == "system":
            if not instructions:
                instructions = text
            else:
                items.append({"role": "developer", "content": text})
        elif role == "user":
            for cid in m.get("_unanswered") or []:
                items.append({"type": "function_call_output", "call_id": cid, "output": "(not run - the user stopped it)"})
            parts = _image_parts(m)
            if parts:
                items.append({"role": "user", "content": ([{"type": "input_text", "text": text}] if text.strip() else [])
                              + parts})
            elif text.strip():
                items.append({"role": "user", "content": text})
        elif role == "assistant":
            if text.strip():
                items.append({"role": "assistant", "content": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {}
                items.append({"type": "function_call", "call_id": tc["_id"], "name": fn.get("name", ""),
                              "arguments": json.dumps(args)})
        elif role == "tool":
            if m.get("_call_id"):
                items.append({"type": "function_call_output", "call_id": m["_call_id"], "output": text or "(done)"})
                if _image_parts(m):
                    later.append({"role": "user", "content": [{"type": "input_text", "text":
                                  f"(the picture from {m.get('tool_name', 'the tool')})"}] + _image_parts(m)})
            else:
                items.append({"role": "user", "content": [{"type": "input_text", "text":
                              f"[result of {m.get('tool_name', 'a tool')}] {text}"}] + _image_parts(m)})
    flush()
    return instructions, items


def tools_for_responses(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for t in tools or []:
        fn = t.get("function") or t
        out.append({"type": "function", "name": fn["name"], "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}, "strict": False})
    return out


# ------------------------------------------------------------------ the request
class OpenAIChat(ExternalChat):
    def __init__(self, req: ChatRequest, events: ChatEvents) -> None:
        super().__init__("openai", req, events)
        self.auth = req.auth
        self._resp: requests.Response | None = None

    def cancel(self) -> None:
        super().cancel()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass

    def _efforts(self) -> list[str | None]:
        steps = EFFORT_STEPS["off" if not self.think else self.effort if self.effort in EFFORT_STEPS else "medium"]
        return list(steps) + [None]

    def _body(self, opts: dict[str, Any]) -> dict[str, Any]:
        instructions, items = to_responses(self.messages)
        body: dict[str, Any] = {"model": self.model, "input": items, "store": False, "stream": True}
        if instructions:
            body["instructions"] = instructions
        tools = tools_for_responses(self.tools) if self.tools and not opts.get("no_tools") else []
        if self.web and not opts.get("no_search"):
            tools.append({"type": "web_search"})       # (OpenAI's own web search - runs on their side)
        if tools:
            body["tools"] = tools
        effort = opts.get("effort", "unset")
        if effort == "unset":
            effort = self._efforts()[0]
        if effort:
            body["reasoning"] = {"effort": effort}
        return body

    def _step_down(self, opts: dict[str, Any], param: str, message: str) -> bool:
        p, low = (param or "").lower(), message.lower()
        learn = _learned.setdefault(f"{self.model}|{self.think}|{self.effort}", {})
        if ("web_search" in low or "web search" in low or "web_search" in p) and self.web \
                and not opts.get("no_search"):
            opts["no_search"] = learn["no_search"] = True
            self.note = "This model can't search the web here, so I answer without web search."
            return True
        if "reasoning" in p or "effort" in p or "reasoning" in low:
            steps = self._efforts()
            cur = opts.get("effort", steps[0])
            i = steps.index(cur) if cur in steps else len(steps) - 1
            if i + 1 >= len(steps):
                return False
            opts["effort"] = learn["effort"] = steps[i + 1]
            return True
        return False

    def run(self) -> None:  # noqa: C901 - one linear request with its error cases
        opts = dict(_learned.get(f"{self.model}|{self.think}|{self.effort}", {}))
        renewed = False
        for _try in range(6):
            if self._cancel:
                return
            try:
                hdr = headers(self.creds, self.auth)
            except OpenAIError as e:
                self.events.failed(e.kind, str(e))
                return
            try:
                with requests.post(_base() + "/responses", json=self._body(opts), headers=hdr, stream=True,
                                   timeout=(20, 600)) as resp:
                    self._resp = resp
                    if resp.status_code >= 400:
                        data = _json_text(resp.text[:4000])
                        if resp.status_code == 401 and self.auth == "chatgpt" and not renewed:
                            renewed = True
                            try:
                                headers(self.creds, self.auth, force=True)
                            except OpenAIError as e:
                                self.events.failed(e.kind, str(e))
                                return
                            continue
                        if self._http_error(resp, data, opts):
                            continue
                        return
                    outcome = self._read(resp, opts)
                    if outcome == "retry":
                        continue
                    return
            except requests.exceptions.ConnectionError as e:
                if not self._cancel:
                    self.events.failed("ext_offline", str(e))
                return
            except requests.exceptions.Timeout:
                self.events.failed("timeout", "")
                return
            except Exception as exc:  # noqa: BLE001
                if not self._cancel:
                    log.exception("OpenAI request failed")
                    self.events.failed("error", str(exc))
                return
        self.events.failed("error", "OpenAI kept refusing the request.")

    def _http_error(self, resp: requests.Response, data: dict[str, Any], opts: dict[str, Any]) -> bool:
        """Report an error answer (True: changed something - ask again)."""
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        code, param = str(err.get("code") or ""), str(err.get("param") or "")
        msg = _err_text(data) or f"HTTP {resp.status_code}"
        st = resp.status_code
        if code == "context_length_exceeded" or is_too_long(msg):
            self.events.failed("too_long", too_long(msg))
        elif code == "subscription_sharing_usage_limit_exceeded":
            self.limit("plan", None, msg)
        elif code == "insufficient_quota":
            self.limit("credits", None, msg)
        elif st == 429:
            self.limit("rate", _openai_reset(resp.headers), msg)
        elif st == 400 and (code in ("unsupported_parameter", "unsupported_value",
                                     "subscription_sharing_unsupported_capability") or param
                            or "web_search" in msg.lower()):
            if self._step_down(opts, param, msg):
                log.info("%s refused %s - trying again with %s", self.model, param or "a setting", opts)
                return True
            self.events.failed("error", msg)
        elif code == "subscription_sharing_user_not_eligible" or st == 403:
            self.events.failed("denied", msg)
        elif st == 401:
            self.events.failed("auth", msg)
        elif st == 404 or code == "model_not_found":
            self.events.failed("model_gone", msg)
        elif st >= 500:
            self.events.failed("busy", msg)
        else:
            self.events.failed("error", msg)
        return False

    def _read(self, resp: requests.Response, opts: dict[str, Any]) -> str:  # noqa: C901 - one event switch
        text: list[str] = []
        calls: list[dict[str, Any]] = []
        thought = 0
        for line in resp.iter_lines(decode_unicode=True):
            if self._cancel:
                return "cancelled"
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            kind = ev.get("type", "")
            if kind == "response.output_text.delta":
                piece = str(ev.get("delta") or "")
                if piece:
                    text.append(piece)
                    self.events.token(piece)
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                thought += len(str(ev.get("delta") or "")) or 1
                self.events.thinking(thought)
            elif kind == "response.output_item.added" and (ev.get("item") or {}).get("type") == "reasoning":
                self.events.thinking(max(1, thought))
            elif kind == "response.output_item.added" and (ev.get("item") or {}).get("type") == "web_search_call":
                self.events.status(SEARCHING)
            elif kind == "response.output_item.done":
                item = ev.get("item") or {}
                if item.get("type") == "function_call":
                    try:
                        args = json.loads(item.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    calls.append({"id": item.get("call_id") or item.get("id"), "name": item.get("name", ""),
                                  "arguments": args if isinstance(args, dict) else {}})
            elif kind == "response.completed":
                r = ev.get("response") or {}
                self._usage(r)
                if not text:
                    text = [_output_text(r)]
                self.raw = None
                self.events.done(with_sources("".join(text), _sources(r)), calls)
                return "done"
            elif kind == "response.incomplete":
                r = ev.get("response") or {}
                self._usage(r)
                why = str((r.get("incomplete_details") or {}).get("reason") or "")
                if why == "content_filter":
                    self.events.failed("refused", why)
                elif calls:
                    self.events.failed("truncated", why)
                else:
                    self.events.done("".join(text) or _output_text(r), [])
                return "done"
            elif kind in ("response.failed", "error"):
                err = (ev.get("response") or {}).get("error") if kind == "response.failed" else ev
                err = err if isinstance(err, dict) else {}
                code, msg = str(err.get("code") or ""), str(err.get("message") or "the answer failed")
                if code == "context_length_exceeded" or is_too_long(msg):
                    self.events.failed("too_long", too_long(msg))
                elif code == "subscription_sharing_usage_limit_exceeded":
                    self.limit("plan", None, msg)
                elif code in ("insufficient_quota",):
                    self.limit("credits", None, msg)
                elif code in ("rate_limit_exceeded",):
                    self.limit("rate", None, msg)
                elif code in ("unsupported_parameter", "unsupported_value", "subscription_sharing_unsupported_capability") \
                        and self._step_down(opts, str(err.get("param") or ""), msg):
                    return "retry"
                else:
                    self.events.failed("busy" if "unavailable" in code or "overloaded" in code else "error", msg)
                return "done"
        if not self._cancel:
            self.events.failed("error", "The answer broke off before it was complete.")
        return "done"

    def _usage(self, r: dict[str, Any]) -> None:
        u = r.get("usage") or {}
        total = int(u.get("total_tokens") or (int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)))
        if total:
            self.events.used("openai", total)


def _output_text(r: dict[str, Any]) -> str:
    out = []
    for item in r.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    out.append(str(part.get("text") or ""))
    return "".join(out)


def _sources(r: dict[str, Any]) -> list[tuple[str, str]]:
    """The web pages a reply cites (web search's url_citation annotations)."""
    out = []
    for item in r.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                for a in (part.get("annotations") or []) if isinstance(part, dict) else []:
                    if isinstance(a, dict) and a.get("type") == "url_citation" and a.get("url"):
                        out.append((str(a.get("title") or ""), str(a["url"])))
    return out


def _json(r: requests.Response) -> dict[str, Any]:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _json_text(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"detail": text}
    except ValueError:
        return {"detail": text}


def _err_text(data: dict[str, Any]) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    return str(err or data.get("detail") or "")


def _duration(text: str) -> float | None:
    """OpenAI's reset durations: "6m0s", "1.5s", "20ms", "1h2m"."""
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", text or ""):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


def _openai_reset(h: Any) -> datetime | None:
    waits = [d for d in (_duration(h.get("x-ratelimit-reset-tokens", "")), _duration(h.get("x-ratelimit-reset-requests", "")))
             if d]
    try:
        ra = float(h.get("retry-after") or 0)
    except ValueError:
        ra = 0
    if ra:
        waits.append(ra)
    return wait_reset(max(waits)) if waits else None
