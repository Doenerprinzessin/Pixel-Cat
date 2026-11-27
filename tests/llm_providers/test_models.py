"""models.py: the local models, which one suits a PC, and model labels."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pixelcat.llm import external
from pixelcat.llm import models as M


@pytest.mark.parametrize("query, tag", [
    ("qwen3.5:9b", "qwen3.5:9b"), ("  QWEN3.5:9B ", "qwen3.5:9b"), ("9B", "qwen3.5:9b"), ("medium", "qwen3.5:9b"),
    ("35b-a3b", "qwen3.5:35b-a3b"), ("Smart", "qwen3.5:35b-a3b"), ("tiny", "qwen3.5:0.8b"),
])
def test_find(query, tag):
    assert M.find(query).tag == tag


@pytest.mark.parametrize("query", ["", "llama3", "anthropic:claude", "qwen3.5"])
def test_find_unknown(query):
    assert M.find(query) is None


@pytest.mark.parametrize("ram, tag", [
    (2, "qwen3.5:0.8b"), (8, "qwen3.5:2b"), (9, "qwen3.5:4b"), (16, "qwen3.5:9b"), (27, "qwen3.5:9b"),
    (28, "qwen3.5:35b-a3b"), (91, "qwen3.5:35b-a3b"), (92, "qwen3.5:122b-a10b"),
])
def test_recommended(ram, tag):
    assert M.recommended(ram).tag == tag


@pytest.mark.parametrize("tag, active", [("qwen3.5:35b-a3b", 3.0), ("qwen3.5:122b-a10b", 10.0),
                                         ("qwen3.5:9b", 9.0), ("qwen3.5:0.8b", 0.8)])
def test_active_b(tag, active):
    assert M.active_b(M.find(tag)) == active


def test_active_b_unknown_size():
    assert M.active_b(M.ModelChoice("x:latest", "X", "X", 1, 1, "")) == 99.0


def hw(ram: float = 32, vram: float = 0, disk: float = 500, avx2: bool = True, cores: int = 8) -> SimpleNamespace:
    return SimpleNamespace(ram_gb=ram, vram_gb=vram, disk_free_gb=disk, avx2=avx2, cores=cores)


@pytest.mark.parametrize("tag, machine, verdict", [
    ("qwen3.5:35b-a3b", hw(32), "ok"),
    ("qwen3.5:35b-a3b", hw(16), "too_big"),
    ("qwen3.5:35b-a3b", hw(32, disk=10), "too_big"),
    ("qwen3.5:9b", hw(16, cores=8), "ok"),
    ("qwen3.5:9b", hw(16, cores=4), "slow"),
    ("qwen3.5:9b", hw(16, avx2=False, cores=16), "slow"),
    ("qwen3.5:9b", hw(16, vram=12, avx2=False), "ok"),           # on the graphics card
    ("qwen3.5:27b", hw(32, cores=16), "slow"),
    ("qwen3.5:4b", hw(8, avx2=True, cores=2), "too_big"),        # 8 GB - 4 GB headroom < 5 GB
    ("qwen3.5:2b", hw(8, avx2=True, cores=2), "ok"),
])
def test_fit(tag, machine, verdict):
    assert M.fit(M.find(tag), machine) == verdict


def test_advise_big_pc_gets_the_default():
    a = M.advise(hw(64))
    assert (a.model.tag, a.online, a.light) == (M.DEFAULT, False, False)
    assert "runs well here" in a.why


def test_advise_medium_pc():
    a = M.advise(hw(16, cores=8))
    assert (a.model.tag, a.online, a.light) == ("qwen3.5:9b", False, True)
    assert "Bigger models would make the PC slow." in a.why and "only loads when you chat" in a.why


def test_advise_small_pc_goes_online():
    a = M.advise(hw(8, cores=2))
    assert (a.model.tag, a.online, a.light) == ("qwen3.5:2b", True, True)
    assert "online AI" in a.why and "Small 2B" in a.why


def test_advise_nothing_fits():
    a = M.advise(hw(4, disk=1))
    assert (a.model, a.online, a.light) == (None, True, True)
    assert "tiny local model" not in a.why


def test_label(monkeypatch):
    monkeypatch.setitem(external.NAMES, "openai:gpt-6.1", "GPT-6.1")
    assert M.label("qwen3.5:9b") == "Medium (9B)"
    assert M.label("openai:gpt-6.1") == "GPT-6.1 (OpenAI)"
    assert M.label("llama3:8b") == "llama3:8b"
