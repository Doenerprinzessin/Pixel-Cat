"""Streaming Ollama /api/chat client, run on a worker thread (the cat never freezes)."""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

from ..ports.llm import ChatEvents, ChatRequest
from ..settings import Config
from .replies import friendly_error, parse_text_tool_calls, strip_tool_markup  # noqa: F401 - (they were here)

log = logging.getLogger(__name__)



class OllamaChat:
    """Sends one chat request and streams tokens back through the events sink
    (``token``, ``thinking``: characters of hidden reasoning so far, ``done``: full content and
    native tool calls, ``failed``).
    ``failed(kind, detail)`` kinds: offline, model_missing, timeout, no_tools, server, error.
    The request's ``cfg`` is the Config (url, options); ``model`` may be empty (the default model).
    """

    def __init__(self, req: ChatRequest, events: ChatEvents) -> None:
        self.events = events
        self.cfg: Config = req.cfg
        self.model = req.model or self.cfg.model   # the chat may use another model than the default
        self.messages = req.messages
        self.tools = req.tools
        self.think = req.think        # decided per request by the app ("auto" mode)
        self.raw: list[dict[str, Any]] | None = None
        self.note = ""
        self._cancel = False
        self._resp: requests.Response | None = None

    def cancel(self) -> None:
        self._cancel = True
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        think = self.think
        for _attempt in range(2):
            retry = self._run_once(think)
            if not retry:
                return
            # the model can't think, or thought too long: ask again without thinking
            think = False if think else None

    def _run_once(self, think: bool | None) -> bool:  # noqa: C901 - linear error handling
        """One request; returns True if it should be retried (without the think flag)."""
        url = self.cfg.ollama_url.rstrip("/") + "/api/chat"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_for_ollama(m) for m in self.messages],
            "stream": True,
            "keep_alive": self.cfg.keep_alive,
            "options": {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx},
        }
        if think is not None:
            payload["think"] = think
        if think:
            payload["options"]["num_predict"] = self.cfg.think_max_tokens
        if self.tools:
            payload["tools"] = self.tools
        content: list[str] = []
        calls: list[dict[str, Any]] = []
        thought = 0
        try:
            with requests.post(url, json=payload, stream=True,
                               timeout=(self.cfg.connect_timeout_s, self.cfg.request_timeout_s)) as resp:
                self._resp = resp
                if resp.status_code != 200:
                    body = resp.text[:500]
                    low = body.lower()
                    if "does not support thinking" in low and think is not None:
                        return True
                    if resp.status_code == 404 or "not found" in low:
                        self.events.failed("model_missing", body)
                    elif "does not support tools" in low:
                        self.events.failed("no_tools", body)
                    else:
                        self.events.failed("server", f"HTTP {resp.status_code}: {body}")
                    return False
                for line in resp.iter_lines(decode_unicode=True):
                    if self._cancel:
                        break
                    if not line:
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        err = str(data["error"])
                        if not content and not calls and (err.strip().upper() == "EOF" or "pars" in err.lower()):
                            # Ollama couldn't parse a tool call the model was writing (a rare glitch,
                            # logged as "tool call parsing failed"): just ask once more
                            log.warning("the model's answer broke off (%s) - asking again", err)
                            return True
                        self.events.failed("no_tools" if "does not support tools" in err else "server", err)
                        return False
                    msg = data.get("message") or {}
                    if msg.get("thinking"):
                        thought += len(msg["thinking"])
                        self.events.thinking(thought)
                    piece = msg.get("content") or ""
                    if piece:
                        content.append(piece)
                        self.events.token(piece)
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        calls.append({"name": fn.get("name", ""), "arguments": args})
                    if data.get("done"):
                        if think and thought and not content and not calls:
                            log.info("thinking used up the answer budget - asking again without thinking")
                            return True
                        used = int(data.get("prompt_eval_count") or 0)
                        log.info("model: %d prompt tokens (context %d) read in %.1f s, %d generated%s", used,
                                 self.cfg.num_ctx, (data.get("prompt_eval_duration") or 0) / 1e9,
                                 int(data.get("eval_count") or 0), f" (thought {thought} chars)" if thought else "")
                        if used >= self.cfg.num_ctx - 64:
                            log.warning("prompt filled the whole context - Ollama may have cut its beginning")
                        break
        except requests.exceptions.ConnectionError:
            if not self._cancel:
                self.events.failed("offline", url)
            return False
        except requests.exceptions.Timeout:
            self.events.failed("timeout", str(self.cfg.request_timeout_s))
            return False
        except Exception as exc:  # noqa: BLE001
            if not self._cancel:
                log.exception("chat request failed")
                self.events.failed("error", str(exc))
            return False
        self.events.done("".join(content), calls)
        return False


def _for_ollama(m: dict[str, Any]) -> dict[str, Any]:
    """A message as Ollama takes it: no external model's own blocks, no tool call ids."""
    out = {k: v for k, v in m.items() if k != "raw"}
    if out.get("tool_calls"):
        out["tool_calls"] = [{"function": tc.get("function") or {}} for tc in out["tool_calls"]]
    return out


def preload_model(cfg: Config) -> None:
    """Load the model in the background (e.g. when the chat opens), so the first answer
    doesn't wait for it.  Same options as real requests, or Ollama would load it again."""
    import threading

    def work() -> None:
        try:
            requests.post(cfg.ollama_url.rstrip("/") + "/api/generate",
                          json={"model": cfg.model, "prompt": "", "keep_alive": cfg.keep_alive,
                                "options": {"num_ctx": cfg.num_ctx}},
                          timeout=(cfg.connect_timeout_s, 180))
        except Exception:  # noqa: BLE001 - only a warm-up
            log.debug("model preload failed", exc_info=True)

    threading.Thread(target=work, daemon=True).start()
