"""What a model writes, in the cat's terms: tool calls written as text instead of native tool calls (some local
models do that), the chat text without them, and the local model's errors in the cat's voice."""
from __future__ import annotations

import json
import re
from typing import Any

_DECODER = json.JSONDecoder()


def _scan_objects(text: str) -> list[tuple[int, int, Any]]:
    """All top-level JSON values starting with '{' in ``text`` as (start, end, obj)."""
    out: list[tuple[int, int, Any]] = []
    i = 0
    while True:
        j = text.find("{", i)
        if j < 0:
            return out
        try:
            obj, end = _DECODER.raw_decode(text, j)
            out.append((j, end, obj))
            i = end
        except json.JSONDecodeError:
            i = j + 1


def _as_call(obj: Any, known: set[str] | None) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
    name = obj.get("name") or obj.get("tool") or fn.get("name")
    args = obj.get("arguments", obj.get("args", obj.get("parameters", fn.get("arguments", {}))))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    if known is not None and name not in known:
        return None
    return {"name": name, "arguments": args}


def parse_text_tool_calls(text: str, known: set[str] | None = None) -> list[dict[str, Any]]:
    """Fallback for models that write tool calls as text instead of native
    tool_calls: <tool_call>{...}</tool_call>, fenced JSON blocks (one or several
    objects) or bare JSON objects naming a known tool."""
    calls = []
    for _a, _b, obj in _scan_objects(text):
        call = _as_call(obj, known)
        if call is not None:
            calls.append(call)
    return calls


def strip_tool_markup(text: str, known: set[str] | None = None) -> str:
    """Hide tool-call JSON/markup from the visible chat text (also while streaming)."""
    spans = [(a, b) for a, b, obj in _scan_objects(text) if _as_call(obj, known) is not None]
    for a, b in reversed(spans):
        text = text[:a] + text[b:]
    text = re.sub(r"</?tool_call>", "", text)
    text = re.sub(r"```[ \t]*(?:json)?[ \t]*\n?\s*```", "", text)  # fences left empty
    return text.strip()


def friendly_error(kind: str, detail: str, model: str, url: str, shell: str = "powershell") -> str:
    """Error messages in the cat's voice.  ``shell``: the command blocks' language tag (the platform's shell)."""
    if kind == "offline":
        return (f"Mrrrow… I can't reach my brain-yarn 🧶 (=;ω;=)\n"
                f"Ollama doesn't seem to be running at {url}. Start the Ollama app (or run the command below), then try again, nyaa~\n"
                f"```{shell}\nollama serve\n```")
    if kind == "model_missing":
        return (f"Mew?! My thinking-model **{model}** is missing 🙀 Please fetch it with:\n"
                f"```{shell}\nollama pull {model}\n```")
    if kind == "timeout":
        return "Purrr… I thought so hard I fell asleep 😿 (the request timed out). Try again, maybe with a shorter question?"
    if kind == "no_tools":
        return "Nyaa, this model can't use tools natively - I'll use my backup paws instead."
    return f"Hiss… something went wrong 😾\n```text\n{detail[:300]}\n```"
