"""pixelcat.adapters.windows.hardware: what this PC can do (memory, graphics cards, CPU, disk) - mocked."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.windows.fakes import FakeDLL, raising
from pixelcat import domain
from pixelcat.adapters.windows import hardware
from pixelcat.adapters.windows.hardware import Gpu, Hardware

DISPLAY = "HKLM\\" + hardware._DISPLAY_CLASS
CPU_KEY = r"HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0"


@pytest.fixture
def kernel32(monkeypatch):  # type: ignore[no-untyped-def]
    k = FakeDLL("kernel32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=k), raising=False)
    return k


# ---------------------------------------------------------------- Hardware (gpu, summary: tests/domain)
def test_reexports_the_domain_types():
    assert hardware.Gpu is domain.Gpu
    assert hardware.Hardware is domain.Hardware


# ---------------------------------------------------------------- gpus (display adapters in the registry)
def test_gpus_from_the_display_adapter_keys(fake_winreg):
    adapters = {
        "0000": {"DriverDesc": "NVIDIA GeForce RTX 3060", "HardwareInformation.qwMemorySize": 12 * 2**30},
        "0001": {"DriverDesc": "Intel(R) UHD Graphics 770", "HardwareInformation.qwMemorySize": 128 * 2**20},
        "0002": {"DriverDesc": "Microsoft Basic Display Adapter", "HardwareInformation.qwMemorySize": 2**30},
        "0003": {"DriverDesc": "AMD Radeon RX 580", "HardwareInformation.MemorySize": (8 * 2**30).to_bytes(8, "little")},
        "0004": {"DriverDesc": "  Old Card  ", "HardwareInformation.MemorySize": b""},
        "0005": {"DriverDesc": "NVIDIA GeForce RTX 3060", "HardwareInformation.qwMemorySize": 12 * 2**30},
        "0006": {"HardwareInformation.qwMemorySize": 2**30},
        "0007": {"DriverDesc": "Parsec Virtual Display Adapter"},
        "Configuration": {"DriverDesc": "not an adapter"},
        "Properties": {},
    }
    for sub, values in adapters.items():
        fake_winreg.add(f"{DISPLAY}\\{sub}", **values)
    assert hardware.gpus() == [
        Gpu("NVIDIA GeForce RTX 3060", 12.0, False),
        Gpu("Intel(R) UHD Graphics 770", 0.1, True),
        Gpu("AMD Radeon RX 580", 8.0, False),
        Gpu("Old Card", 0.0, True),
    ]


def test_gpus_without_the_display_class(fake_winreg):
    assert hardware.gpus() == []


def test_gpus_without_winreg(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)
    assert hardware.gpus() == []


# ---------------------------------------------------------------- cpu / ram / disk
@pytest.mark.parametrize("name, avx2, expected", [
    ("  Intel(R) Core(TM) i7-9700K   CPU @ 3.60GHz ", 1, ("Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz", True)),
    (None, 0, ("", False)),
])
def test_cpu(fake_winreg, kernel32, monkeypatch, name, avx2, expected):
    if name is not None:
        fake_winreg.add(CPU_KEY, ProcessorNameString=name)
    kernel32.IsProcessorFeaturePresent.result = lambda feature: avx2 if feature == 40 else 0
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    assert hardware.cpu() == (expected[0], 12, expected[1])


def test_cpu_assumes_avx2_when_it_cannot_ask(fake_winreg, kernel32, monkeypatch):
    kernel32.IsProcessorFeaturePresent.result = raising(OSError("x"))
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert hardware.cpu() == ("", 2, True)


def test_ram_gb(kernel32):
    def status(p):  # type: ignore[no-untyped-def]
        m = p._obj
        assert m.len == ctypes.sizeof(m)
        m.total = 32 * 2**30 - 300 * 2**20
        return 1

    kernel32.GlobalMemoryStatusEx.result = status
    assert hardware.ram_gb() == pytest.approx(31.707, abs=1e-3)
    kernel32.GlobalMemoryStatusEx.result = raising(OSError("x"))
    assert hardware.ram_gb() == 16.0


def test_models_dir(monkeypatch, tmp_path):
    assert hardware.models_dir() == Path.home() / ".ollama" / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "m"))
    assert hardware.models_dir() == tmp_path / "m"


def test_disk_free_gb_looks_at_the_nearest_existing_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "not" / "yet" / "there"))
    seen = []
    monkeypatch.setattr(hardware.shutil, "disk_usage", lambda p: seen.append(p) or SimpleNamespace(free=50 * 2**30))
    assert hardware.disk_free_gb() == 50.0
    assert seen == [tmp_path]
    monkeypatch.setattr(hardware.shutil, "disk_usage", raising(OSError("x")))
    assert hardware.disk_free_gb() == 100.0


def test_detect_is_cached_until_fresh(monkeypatch):
    monkeypatch.setattr(hardware, "_cached", None)
    calls = []
    monkeypatch.setattr(hardware, "cpu", lambda: calls.append("cpu") or ("CPU X", 8, False))
    monkeypatch.setattr(hardware, "ram_gb", lambda: 31.9)
    monkeypatch.setattr(hardware, "gpus", lambda: [Gpu("Card", 8.0, False)])
    monkeypatch.setattr(hardware, "disk_free_gb", lambda: 77.0)
    hw = hardware.detect()
    assert hw == Hardware(31.9, 8, "CPU X", False, [Gpu("Card", 8.0, False)], 77.0)
    assert hardware.detect() is hw and calls == ["cpu"]
    assert hardware.detect(fresh=True) is not hw and calls == ["cpu", "cpu"]
