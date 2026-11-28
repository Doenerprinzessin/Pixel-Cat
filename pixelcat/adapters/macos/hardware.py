"""What this Mac can do for the local AI: memory, cores, CPU, free disk (sysctl, shutil.disk_usage).

Apple Silicon has *unified memory*: the GPU works in the same RAM as everything else, and Ollama runs models
on it through Metal.  ``llm/models.fit`` adds a dedicated card's own memory on top of the RAM, so reporting the
GPU as a card would count the same memory twice.  So on a Mac ``gpus`` stays empty and the models are judged
by RAM and cores alone: ``avx2=True`` stands for "fast vector maths" (NEON / Metal on arm64; the real AVX2 flag
on Intel Macs).  That gives sensible choices: 8 GB -> an online AI, 16 GB -> the 9b model, 32 GB and more ->
the 35b-a3b mixture of experts.  ``cores`` is ``hw.physicalcpu`` (on Apple Silicon the same as the logical
cores; on Intel Macs the real cores, without Hyper-Threading).
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path

from . import _objc
from ...domain import Hardware

log = logging.getLogger(__name__)


def sysctl(name: str) -> str:
    """``sysctl -n <name>``; "" if it fails or the name doesn't exist."""
    try:
        res = _objc.run(["/usr/sbin/sysctl", "-n", name], timeout=5)
    except Exception as exc:  # noqa: BLE001 - OSError, subprocess.TimeoutExpired
        log.debug("sysctl %s failed: %s", name, exc)
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _int(text: str, default: int) -> int:
    try:
        return int(text.split()[0])
    except (IndexError, ValueError):
        return default


def ram_gb() -> float:
    total = _int(sysctl("hw.memsize"), 0)
    return total / 2**30 if total > 0 else 16.0


def cpu() -> tuple[str, int, bool]:
    name = " ".join(sysctl("machdep.cpu.brand_string").split())
    cores = _int(sysctl("hw.physicalcpu"), 0) or os.cpu_count() or 2
    arm = platform.machine() == "arm64" or sysctl("hw.optional.arm64") == "1"   # (also under Rosetta)
    avx2 = True if arm else sysctl("hw.optional.avx2_0") == "1"
    return name, cores, avx2


def models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def disk_free_gb() -> float:
    p = models_dir()
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 2**30
    except OSError:
        return 100.0


_cached: Hardware | None = None


def detect(fresh: bool = False) -> Hardware:
    """This Mac's hardware (looked at once unless ``fresh``)."""
    global _cached
    if _cached is None or fresh:
        name, cores, avx2 = cpu()
        _cached = Hardware(ram_gb(), cores, name, avx2, [], disk_free_gb())
        log.info("this Mac: %s; CPU %s; %.0f GB free for models", _cached.summary(), name or "?",
                 _cached.disk_free_gb)
    return _cached
