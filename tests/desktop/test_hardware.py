"""pixelcat.desktop.hardware: what this PC can do (memory, graphics cards, CPU, disk) - mocked."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeDLL, raising
from pixelcat.desktop import hardware
from pixelcat.desktop.hardware import Gpu, Hardware

DISPLAY = "HKLM\\" + hardware._DISPLAY_CLASS
CPU_KEY = r"HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0"


@pytest.fixture
def kernel32(monkeypatch):  # type: ignore[no-untyped-def]
    k = FakeDLL("kernel32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=k), raising=False)
    return k


# ---------------------------------------------------------------- Hardware
@pytest.mark.parametrize("gpus, best", [
    ([Gpu("Intel(R) UHD Graphics", 0.1, True), Gpu("NVIDIA GeForce RTX 3060", 12.0, False),
      Gpu("NVIDIA GeForce GTX 1650", 4.0, False)], "NVIDIA GeForce RTX 3060"),
    ([Gpu("AMD Radeon RX 6400", 1.9, False)], None),                 # too little memory to help
    ([Gpu("Big APU", 16.0, True)], None),                            # integrated never counts
    ([], None),
])
def test_hardware_gpu_is_the_biggest_dedicated_card(gpus, best):
    hw = Hardware(gpus=gpus)
    assert (hw.gpu.name if hw.gpu else None) == best
    assert hw.vram_gb == (hw.gpu.vram_gb if hw.gpu else 0.0)


def test_hardware_summary():
    hw = Hardware(ram_gb=15.7, cores=8, gpus=[Gpu("NVIDIA GeForce RTX 3060", 12.0, False)])
    assert hw.summary() == "16 GB memory · NVIDIA GeForce RTX 3060 (12 GB) · 8 cores"
    assert Hardware(ram_gb=8, cores=4).summary() == "8 GB memory · no graphics card for AI · 4 cores"


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
