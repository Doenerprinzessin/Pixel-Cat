"""What this PC can do (for choosing an AI model): memory, graphics card, CPU, free disk.

Everything is read from Windows itself - no admin rights, nothing installed:
* memory: GlobalMemoryStatusEx
* graphics cards and their own memory: the display adapters' registry entries
  (HardwareInformation.qwMemorySize - exact also above 4 GB, unlike WMI's AdapterRAM)
* CPU: its name, logical cores, and whether it has AVX2 (the AI engine is much slower without)
* free disk space where Ollama keeps its models
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
from pathlib import Path

from ..domain.hardware import Gpu, Hardware  # noqa: F401 - re-exported

log = logging.getLogger(__name__)

_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_NOT_A_GPU = ("basic display", "basic render", "remote display", "virtual", "hyper-v", "parsec", "idd", "meta",
              "displaylink", "citrix", "vmware", "virtualbox", "spacedesk")
_INTEGRATED = ("intel(r) uhd", "intel(r) hd", "intel(r) iris", "intel uhd", "intel hd", "intel iris",
               "amd radeon(tm) graphics", "radeon(tm) graphics", "radeon graphics", "adreno", "qualcomm",
               "intel(r) arc(tm) graphics", "intel(r) graphics")


def ram_gb() -> float:
    try:
        class _MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ax", ctypes.c_ulonglong)]

        m = _MS()
        m.len = ctypes.sizeof(_MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        # (a "16 GB" PC reports a little less - some is reserved for the hardware)
        return m.total / 2**30
    except Exception:  # noqa: BLE001
        return 16.0


def gpus() -> list[Gpu]:
    out: list[Gpu] = []
    try:
        import winreg
    except ImportError:
        return out
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return out
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    name = _value(k, "DriverDesc") or ""
                    size = _value(k, "HardwareInformation.qwMemorySize")
                    if size is None:
                        size = _value(k, "HardwareInformation.MemorySize")
            except OSError:
                continue
            if isinstance(size, bytes):
                size = int.from_bytes(size[:8], "little") if size else 0
            if not name or any(w in name.lower() for w in _NOT_A_GPU):
                continue
            vram = float(size or 0) / 2**30
            low = name.lower()
            integrated = any(w in low for w in _INTEGRATED) or vram < 1.0
            if not any(g.name == name for g in out):
                out.append(Gpu(name.strip(), round(vram, 1), integrated))
    return out


def _value(key, name: str):  # type: ignore[no-untyped-def]
    import winreg

    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def cpu() -> tuple[str, int, bool]:
    name = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    try:
        avx2 = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))   # PF_AVX2_INSTRUCTIONS_AVAILABLE
    except Exception:  # noqa: BLE001
        avx2 = True
    return " ".join(name.split()), os.cpu_count() or 2, avx2


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
    global _cached
    if _cached is None or fresh:
        name, cores, avx2 = cpu()
        _cached = Hardware(ram_gb(), cores, name, avx2, gpus(), disk_free_gb())
        log.info("this PC: %s; CPU %s (AVX2 %s); %.0f GB free for models", _cached.summary(), name or "?",
                 "yes" if avx2 else "no", _cached.disk_free_gb)
    return _cached
