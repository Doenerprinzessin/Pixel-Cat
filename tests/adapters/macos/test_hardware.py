"""pixelcat.adapters.macos.hardware: sysctl figures, and Apple Silicon judged by its unified memory."""
from __future__ import annotations

import pytest

from adapters.macos.fakes import FakeRun
from pixelcat.adapters.macos import _objc, hardware
from pixelcat.llm import models


@pytest.fixture(autouse=True)
def fresh(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(hardware, "_cached", None)


def _mac(fake_run, monkeypatch, ram_gb: int, cores: int, machine: str = "arm64", avx2: str = "") -> None:  # type: ignore[no-untyped-def]
    answers = {"hw.memsize": str(ram_gb * 2**30), "hw.physicalcpu": str(cores),
               "machdep.cpu.brand_string": "Apple  M3 Pro", "hw.optional.arm64": "0", "hw.optional.avx2_0": avx2}
    for name, value in answers.items():
        fake_run.answer(["/usr/sbin/sysctl", "-n", name], value + "\n", returncode=0 if value else 1)
    monkeypatch.setattr(hardware.platform, "machine", lambda: machine)


def test_detect_reads_sysctl(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    _mac(fake_run, monkeypatch, 36, 12)
    hw = hardware.detect()
    assert (hw.ram_gb, hw.cores, hw.cpu, hw.avx2, hw.gpus) == (36.0, 12, "Apple M3 Pro", True, [])
    assert hw.disk_free_gb > 0
    calls = len(fake_run.calls)
    assert hardware.detect() is hw and len(fake_run.calls) == calls      # looked at once
    assert hardware.detect(fresh=True) is not hw


def test_intel_macs_report_their_avx2(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    _mac(fake_run, monkeypatch, 16, 4, machine="x86_64", avx2="1")
    assert hardware.detect().avx2 is True
    fake_run = FakeRun()
    monkeypatch.setattr(_objc, "run", fake_run)
    _mac(fake_run, monkeypatch, 16, 4, machine="x86_64", avx2="0")
    assert hardware.detect(fresh=True).avx2 is False


def test_sysctl_failures_fall_back_to_the_defaults(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    fake_run.answer(["/usr/sbin/sysctl"], raises=OSError("no sysctl"))
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    hw = hardware.detect()
    assert hw.ram_gb == 16.0 and hw.cores >= 1 and hw.cpu == ""


@pytest.mark.parametrize("ram, cores, tag, online", [
    (8, 8, "qwen3.5:2b", True),             # a base MacBook Air: an online AI keeps it fast
    (16, 8, "qwen3.5:9b", False),
    (36, 12, "qwen3.5:35b-a3b", False),     # unified memory: the RAM is what counts
])
def test_apple_silicon_gets_sensible_models(fake_run, monkeypatch, ram, cores, tag, online):  # type: ignore[no-untyped-def]
    _mac(fake_run, monkeypatch, ram, cores)
    monkeypatch.setattr(hardware, "disk_free_gb", lambda: 200.0)
    advice = models.advise(hardware.detect())
    assert advice.model is not None and (advice.model.tag, advice.online) == (tag, online)


@pytest.mark.macos
def test_live_hardware():
    hw = hardware.detect(fresh=True)
    assert hw.ram_gb >= 4 and hw.cores >= 2 and hw.cpu and hw.gpus == []
