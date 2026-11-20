"""The AI models the user can pick (Settings -> AI model, and per chat).

All are Qwen 3.5 - the same family, so every feature works with each of them (seeing
screenshots, using tools, thinking); bigger ones are smarter but need more memory.
``ram_gb`` is what the model needs while it is loaded with the 64k context.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelChoice:
    tag: str
    name: str
    short: str
    download_gb: float
    ram_gb: float
    note: str


MODELS: list[ModelChoice] = [
    ModelChoice("qwen3.5:0.8b", "Tiny", "0.8B", 1.2, 2.5, "runs on any PC - basic answers"),
    ModelChoice("qwen3.5:2b", "Small", "2B", 2.7, 4.0, "older or small laptops"),
    ModelChoice("qwen3.5:4b", "Light", "4B", 3.3, 5.0, "8 GB PCs - quick, decent"),
    ModelChoice("qwen3.5:9b", "Medium", "9B", 6.6, 9.0, "16 GB PCs - good all-rounder"),
    ModelChoice("qwen3.5:27b", "Large", "27B", 17.0, 20.0, "smart, but slow without a strong graphics card"),
    ModelChoice("qwen3.5:35b-a3b", "Smart", "35B", 22.0, 24.0, "32 GB+ PCs - smart and still fast (the default)"),
    ModelChoice("qwen3.5:122b-a10b", "Huge", "122B", 81.0, 88.0, "workstations with 96 GB+ of memory"),
]
DEFAULT = "qwen3.5:35b-a3b"


def find(tag: str) -> ModelChoice | None:
    t = tag.strip().lower()
    for m in MODELS:
        if m.tag == t or m.short.lower() == t or m.name.lower() == t or m.tag.split(":")[1] == t:
            return m
    return None


def recommended(ram_gb: float) -> ModelChoice:
    """The best model that comfortably fits this PC's memory (memory only - see advise())."""
    best = MODELS[0]
    for m in MODELS:
        if m.ram_gb + 4 <= ram_gb and m.tag != "qwen3.5:27b":   # 27b is dense: slow on most PCs
            best = m
    return best


# ---------------------------------------------------------------- what suits this PC
PREFERRED = ["qwen3.5:35b-a3b", "qwen3.5:9b", "qwen3.5:27b", "qwen3.5:4b", "qwen3.5:2b", "qwen3.5:0.8b"]
SMALL = {"qwen3.5:2b", "qwen3.5:0.8b"}        # if only these fit, an online AI is the better choice


def active_b(m: ModelChoice) -> float:
    """Billions of parameters used per word: 35b-a3b -> 3 (a mixture of experts), 9b -> 9."""
    import re

    size = m.tag.split(":")[-1]
    mo = re.search(r"a(\d+(?:\.\d+)?)b", size) or re.search(r"(\d+(?:\.\d+)?)b", size)
    return float(mo.group(1)) if mo else 99.0


def fit(m: ModelChoice, hw) -> str:  # type: ignore[no-untyped-def]
    """"ok" (fits with room for your other apps, and answers fast enough), "slow" (fits, but
    the CPU can't keep up), or "too_big" (the PC would have to swap and get very slow)."""
    headroom = 4.0 if hw.ram_gb < 12 else 6.0       # Windows, the browser, your other apps
    vram = hw.vram_gb
    room = max(0.0, hw.ram_gb - headroom) + 0.9 * vram
    if m.ram_gb > room or hw.disk_free_gb < m.download_gb + 2:
        return "too_big"
    on_card = m.ram_gb <= 0.9 * vram + 1.0           # (almost) all of it on the graphics card
    active = active_b(m)
    by_cpu = hw.avx2 and (active <= 4.5 or (active <= 10 and hw.cores >= 8))
    return "ok" if on_card or by_cpu else "slow"


@dataclass(frozen=True)
class Advice:
    model: ModelChoice | None     # the local model that suits this PC (None: none does)
    online: bool                  # an online AI (ChatGPT / Claude) is the better choice here
    light: bool                   # keep the AI out of memory until the chat needs it
    why: str                      # in plain words


def advise(hw) -> Advice:  # type: ignore[no-untyped-def]
    """What to use on this PC: the best local model that fits and is fast enough - or, if only
    the smallest ones (or none) would, an online AI so the PC stays fast."""
    best = next((m for t in PREFERRED for m in MODELS if m.tag == t and fit(m, hw) == "ok"), None)
    light = hw.ram_gb < 24
    if best is None or best.tag in SMALL:
        small = f" A tiny local model ({best.name} {best.short}) works, but it's slow and its answers are basic." \
            if best is not None else ""
        why = ("This PC doesn't have the memory or graphics card for a good local AI - it would make the PC lag. "
               "An online AI (ChatGPT or Claude) thinks on their servers instead, so your PC stays fast." + small)
        return Advice(best, True, True, why)
    if best.tag == DEFAULT:
        why = f"{best.name} {best.short} runs well here: it fits in memory with room to spare and answers quickly."
    else:
        why = (f"{best.name} {best.short} fits this PC with room left for your other apps and answers at a good "
               "speed. Bigger models would make the PC slow.")
    if light:
        why += " To keep the PC fast, the AI only loads when you chat."
    return Advice(best, False, light, why)


def label(tag: str) -> str:
    m = find(tag)
    if m:
        return f"{m.name} ({m.short})"
    from .external import label as ext_label   # anthropic:... / openai:...

    return ext_label(tag)
