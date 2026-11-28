"""The conversation context sent to the model: token estimates, the history split into exchanges and
rounds, older exchanges condensed, and the scrubbing of replies.  Plain Python (no Qt)."""
from __future__ import annotations

import json
import re
from typing import Any

from ..llm.replies import strip_tool_markup

NOTE_RE = re.compile(r"\((?:Steps I took|steps i took):[^)]*\)\s*|\[memory note:[^\]]*\]\s*", re.I)
# replies that promise to act later ("I'll let you know when it arrives") - only possible with wait_for
PROMISE_RE = re.compile(
    r"\bI(?:'ll|\s+will|\s+am going to|'m going to|\s+shall)\s+(?:\w+\s+){0,3}?(?:wait|watch|monitor|keep (?:an eye|"
    r"watching|checking|looking|monitoring)|let you know|notify you|tell you (?:when|as soon|once|if|right away)|"
    r"ping you|alert you|check (?:back|again|in|later)|remind you|get back to you|be (?:waiting|watching|on the "
    r"lookout))|\bI'm (?:now )?(?:waiting|watching|monitoring|keeping an eye)"
    r"|\bich\s+(?:werde\s+)?(?:\w+\s+){0,3}?(?:warte|warten|beobachte|beobachten|überwache|überwachen|"
    r"sage? dir (?:dann |sofort )?Bescheid|gebe? dir (?:dann |sofort )?Bescheid|melde mich|informiere dich|"
    r"erinnere dich|behalte\b.{0,40}\bim Auge|schaue? (?:später|nochmal|wieder))"
    r"|\b(?:sage|gebe) ich dir\b.{0,20}\bBescheid|\bmelde ich mich\b|\bwarte ich\b|\binformiere ich dich\b"
    r"|\bhalte ich\b.{0,30}\bAusschau", re.I)
# ...but waiting for the user's own answer is fine
USER_WAIT_RE = re.compile(r"\byour (?:answer|reply|response|decision|ok|okay|confirmation|go-ahead)\b|"
                           r"\bdeine (?:Antwort|Rückmeldung|Entscheidung|Bestätigung)\b", re.I)


def scrub(text: str) -> str:
    """Remove memory-note lookalikes (from older versions) from answers."""
    return NOTE_RE.sub("", text)


def tokens(msgs: list[dict[str, Any]]) -> int:
    """Rough token estimate (about 3 characters per token; a screenshot ~1,600 tokens)."""
    return sum(len(str(m.get("content", ""))) // 3 + 8 + 1600 * len(m.get("images") or [])
               + len(json.dumps(m.get("tool_calls", ""))) // 3 for m in msgs)


def turns(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The history split into exchanges: each starts with something the user wrote."""
    turns: list[list[dict[str, Any]]] = []
    for m in history:
        if m["role"] == "user" and not m.get("_tool") and not (m.get("_continue") and turns):
            turns.append([m])
        elif turns:
            turns[-1].append(m)
    return turns


def summarize(turn: list[dict[str, Any]], native: bool = True) -> list[dict[str, Any]]:
    """An earlier exchange, condensed: the request, its last few tool steps (kept as real
    tool calls with one-line results, so the model keeps *calling* tools rather than writing
    results as text) and the answer.  Screenshots and long tool output are dropped."""
    out: list[dict[str, Any]] = [{"role": "user", "content": turn[0]["content"]}]
    steps: list[tuple[dict[str, Any], dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []
    for m in turn[1:]:
        if m["role"] == "assistant" and m.get("tool_calls"):
            pending = list(m["tool_calls"])
        elif m["role"] == "tool" and pending:
            steps.append((pending.pop(0), m))
    if native:
        for call, res in steps[-4:]:
            fn = call.get("function", {})
            args = {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v)
                    for k, v in (fn.get("arguments") or {}).items()}
            first = str(res.get("content", "")).split("\n")[0]
            out.append({"role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": fn.get("name", ""), "arguments": args}}]})
            out.append({"role": "tool", "tool_name": res.get("tool_name", fn.get("name", "")),
                        "content": first[:160] + (" ..." if len(first) > 160 else "")})
    answer = ""
    for m in reversed(turn):
        if m["role"] == "assistant" and str(m.get("content", "")).strip():
            answer = strip_tool_markup(str(m["content"]))
            break
    out.append({"role": "assistant", "content": scrub(answer)[:1500].strip() or "(done)"})
    return out


def rounds(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Steps of a task grouped into rounds: one model message plus its tool results
    (a "continue" from the user is a round of its own)."""
    rounds: list[list[dict[str, Any]]] = []
    for m in steps:
        if m["role"] == "assistant" or (m["role"] == "user" and not m.get("_tool")) or not rounds:
            rounds.append([m])
        else:
            rounds[-1].append(m)
    return rounds


def clean(m: dict[str, Any], cap: int | None = None, keep_images: bool = True) -> dict[str, Any]:
    out = {k: v for k, v in m.items() if not k.startswith("_") and (keep_images or k != "images")}
    text = str(out.get("content", ""))
    if cap is not None and len(text) > cap:
        out["content"] = text[:cap] + " ...(shortened)"
    return out


def round_form(rnd: list[dict[str, Any]], age: int) -> list[dict[str, Any]]:
    """A round as sent to the model: the newest in full, older ones shorter and shorter."""
    if age == 0:
        return [clean(m) for m in rnd]
    if age < 3:
        return [clean(m, 1500 if (m["role"] == "tool" or m.get("_tool")) else None, False) for m in rnd]
    out = []
    for m in rnd:
        c = clean(m, None, False)
        c.pop("raw", None)                           # (shortened: not the model's own blocks any more)
        if m["role"] == "tool" or m.get("_tool"):
            first = str(c.get("content", "")).split("\n")[0]
            c["content"] = first[:160] + (" ..." if len(first) > 160 else "")
        elif m["role"] == "assistant":
            c["content"] = str(c.get("content", ""))[:300]
            for tc in c.get("tool_calls") or []:
                fn = tc.get("function", {})
                fn["arguments"] = {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v)
                                   for k, v in (fn.get("arguments") or {}).items()}
        out.append(c)
    return out
