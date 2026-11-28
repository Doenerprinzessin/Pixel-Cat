"""model_status: finding and starting Ollama, the model on disk, and ModelStatus's start-up
(check -> start Ollama -> download -> load) against a fake Ollama on 127.0.0.1."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from adapters.llm.fakes import Json, Lines, Raw
from pixelcat.llm import model_status as MS
from pixelcat.settings import Config

MODEL = "qwen3.5:9b"


# ------------------------------------------------------------------ Ollama on this PC
@pytest.mark.parametrize("files, which, expected", [
    (["ollama app.exe", "ollama.exe"], None, "ollama app.exe"),
    (["ollama.exe"], "/usr/bin/ollama", "ollama.exe"),
    ([], "/usr/bin/ollama", "/usr/bin/ollama"),
    ([], None, None),
])
def test_find_ollama_app(monkeypatch, files, which, expected):
    base = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama"
    base.mkdir(parents=True)
    for f in files:
        (base / f).write_bytes(b"")
    monkeypatch.setattr(MS.shutil, "which", lambda name: which)
    found = MS.find_ollama_app()
    if expected is None:
        assert found is None
    elif expected.startswith("/"):
        assert found == Path(expected)
    else:
        assert found == base / expected


def test_model_on_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path))
    assert MS.models_dir() == tmp_path
    reg = tmp_path / "manifests" / "registry.ollama.ai"
    for rel in ("library/qwen3.5/35b-a3b", "library/cat/latest", "someone/dog/7b"):
        (reg / rel).parent.mkdir(parents=True, exist_ok=True)
        (reg / rel).write_text("{}")
    assert MS.model_on_disk("qwen3.5:35b-a3b")
    assert MS.model_on_disk("cat") and MS.model_on_disk("cat:latest")
    assert MS.model_on_disk("someone/dog:7b")
    assert not MS.model_on_disk("qwen3.5:9b") and not MS.model_on_disk("dog:7b")


def test_models_dir_default():
    assert MS.models_dir() == Path.home() / ".ollama" / "models"


@pytest.mark.parametrize("a, b, same", [("cat", "cat:latest", True), ("Cat:Latest ", "cat", True),
                                        ("qwen3.5:9b", "qwen3.5:9b", True), ("qwen3.5:9b", "qwen3.5:4b", False)])
def test_same_model(a, b, same):
    assert MS.same_model(a, b) is same


@pytest.mark.parametrize("url, local", [("http://localhost:11434", True), ("http://127.0.0.1:1/", True),
                                        ("http://[::1]:11434", True), ("http://0.0.0.0", True),
                                        ("http://ollama.lan:11434", False), ("nonsense", False)])
def test_is_local(url, local):
    assert MS.is_local(url) is local


@pytest.mark.parametrize("name, args, flags", [
    ("ollama app.exe", ["{exe}"], 0x08 | 0x200),
    ("ollama.exe", ["{exe}", "serve"], 0x08 | 0x200 | 0x08000000),
])
def test_start_ollama(monkeypatch, tmp_path, name, args, flags):
    calls = []

    def popen(a, **kw):
        calls.append((a, kw))
        if len(calls) == 1:
            raise OSError("breakaway not allowed")
    monkeypatch.setattr(MS.subprocess, "Popen", popen)
    exe = tmp_path / name
    MS.start_ollama(exe)
    assert [c[0] for c in calls] == [[a.format(exe=exe) for a in args]] * 2
    assert [c[1]["creationflags"] for c in calls] == [flags | 0x01000000, flags]
    kw = calls[0][1]
    assert kw["cwd"] == str(tmp_path) and kw["stdin"] == subprocess.DEVNULL and kw["close_fds"] is True


def test_start_ollama_gives_up_quietly(monkeypatch, tmp_path):
    def popen(a, **kw):
        raise OSError("no")
    monkeypatch.setattr(MS.subprocess, "Popen", popen)
    MS.start_ollama(tmp_path / "ollama.exe")


# ------------------------------------------------------------------ ModelStatus
@pytest.fixture
def ollama(fake_http):
    fake_http.on("GET", "/api/version", Json(200, {"version": "0.12"}))
    fake_http.on("GET", "/api/tags", Json(200, {"models": [{"name": MODEL}]}))
    fake_http.on("GET", "/api/ps", Json(200, {"models": []}))
    fake_http.on("POST", "/api/generate", Json(200, {}))
    return fake_http


class Status:
    """A ModelStatus with recorded states; ``started``: the Ollama starts asked for; ``on_change``: what
    its ``notify()`` calls."""

    def __init__(self, url: str, may_download: bool = False, exe: Path | None = None, starter=None,
                 **cfg) -> None:
        self.started: list[Path] = []
        self.seen: list[str] = []
        self.on_change: list[Callable[[], None]] = [lambda: self.seen.append(self.st.state)]
        self.st = MS.ModelStatus(Config(ollama_url=url, model=MODEL, keep_alive="3m", num_ctx=2048, **cfg),
                                 lambda: may_download, lambda: exe, starter or self.started.append,
                                 notify=lambda: [f() for f in self.on_change])

    def run(self) -> MS.ModelStatus:
        self.st._run()
        return self.st


@pytest.fixture
def status():
    return Status


def test_ready_after_loading(ollama, status):
    s = status(ollama.url)
    st = s.run()
    assert s.seen == ["checking", "loading", "ready"] and st.ready() and st.banner() is None
    assert ollama.sent("/api/generate")[0].json() == {"model": MODEL, "prompt": "", "keep_alive": "3m",
                                                      "options": {"num_ctx": 2048}}


def test_already_loaded_is_ready_at_once(ollama, status):
    ollama.on("GET", "/api/ps", Json(200, {"models": [{"name": MODEL}]}))
    s = status(ollama.url)
    s.run()
    assert s.seen == ["checking", "ready"] and ollama.sent("/api/generate") == []
    s.run()
    assert s.seen == ["checking", "ready", "ready"]             # (no 'checking' once ready)


def test_external_models_need_nothing(ollama, status):
    s = status(ollama.url)
    s.st.override = "anthropic:claude-opus-5-5"
    s.run()
    assert s.seen == ["ready"] and ollama.requests == [] and s.st.model == "anthropic:claude-opus-5-5"


def test_online_only_pc(ollama, status):
    s = status(ollama.url, ai_mode="online")
    s.run()
    assert s.seen == ["online_only"] and ollama.requests == []
    assert s.st.banner() == MS.ONLINE_ONLY and s.st.short() == ""
    s.st.override = "qwen3.5:4b"
    ollama.on("GET", "/api/tags", Json(200, {"models": [{"name": "qwen3.5:4b"}]}))
    s.run()
    assert s.st.state == "ready"


def test_no_ollama_installed(ollama, status):
    ollama.on("GET", "/api/version", Raw(500, b""))
    s = status(ollama.url)
    s.run()
    assert s.seen == ["checking", "no_ollama"] and s.started == []
    assert "isn't installed" in s.st.banner() and s.st.short() == "AI not ready"


def test_remote_ollama_offline(status, monkeypatch):
    def unreachable(*a, **kw):
        raise MS.requests.ConnectionError("no route")
    monkeypatch.setattr(MS.requests, "get", unreachable)
    s = status("http://10.255.255.1:9", exe=Path("ollama.exe"))
    s.run()
    assert s.seen == ["checking", "offline"] and s.started == []
    assert s.st.banner() == "I can't reach Ollama at http://10.255.255.1:9 - is it running?"


def test_ollama_is_started_when_installed(ollama, status, tmp_path):
    ollama.on("GET", "/api/version", Raw(500, b""))

    def start(exe: Path) -> None:
        s.started.append(exe)
        ollama.on("GET", "/api/version", Json(200, {}))
    s = status(ollama.url, exe=tmp_path / "ollama app.exe", starter=start)
    s.run()
    assert s.started == [tmp_path / "ollama app.exe"]
    assert s.seen == ["checking", "starting_ollama", "loading", "ready"]


def test_ollama_that_doesnt_come_up_is_offline_when_stopped(ollama, status, tmp_path):
    ollama.on("GET", "/api/version", Raw(500, b""))
    s = status(ollama.url, exe=tmp_path / "ollama.exe", starter=lambda exe: s.st.stop())
    s.run()
    assert s.seen == ["checking", "starting_ollama", "offline"]


def test_missing_model_without_permission(ollama, status):
    ollama.on("GET", "/api/tags", Json(200, {"models": [{"name": "other:1b"}]}))
    s = status(ollama.url)
    s.run()
    assert s.seen == ["checking", "missing"] and ollama.sent("/api/pull") == []
    assert "/getmodel" in s.st.banner()


def test_download_then_load(ollama, status):
    ollama.on("GET", "/api/tags", Json(500, {}))
    ollama.on("POST", "/api/pull", Lines([
        {"status": "pulling manifest"},
        {"status": "downloading", "digest": "sha256:a", "total": 3_000_000_000, "completed": 1_000_000_000},
        {"status": "downloading", "digest": "sha256:b", "total": 1_000_000_000},
        b"",
        {"status": "success"}]))
    s = status(ollama.url, may_download=True)
    states: list[tuple] = []
    s.on_change.append(lambda: states.append((s.st.state, s.st.done_bytes, s.st.total_bytes, s.st.banner())))
    st = s.run()
    assert ollama.sent("/api/pull")[0].json() == {"model": MODEL, "stream": True}
    assert states[1] == ("downloading", 0, 0, f"⬇ Downloading the AI model ({MODEL})…")
    assert ("downloading", 1_000_000_000, 3_000_000_000,
            f"⬇ Downloading the AI model ({MODEL}): 33% - 1.0 of 3.0 GB. The chat works once it's done.") in states
    assert states[-3][:3] == ("downloading", 4_000_000_000, 4_000_000_000)
    assert [x[0] for x in states][-2:] == ["loading", "ready"] and st.ready()


@pytest.mark.parametrize("pull, error", [
    (Raw(500, b"no space"), "download failed: no space"),
    (Lines([{"status": "pulling"}, {"error": "manifest unknown"}]), "download failed: manifest unknown"),
    (Lines([{"status": "pulling"}]), "the download stopped before it was complete - it continues next time"),
])
def test_download_failures(ollama, status, pull, error):
    ollama.on("GET", "/api/tags", Json(200, {"models": []}))
    ollama.on("POST", "/api/pull", pull)
    s = status(ollama.url, may_download=True)
    s.run()
    assert (s.st.state, s.st.error) == ("error", error)
    assert ollama.sent("/api/generate") == []
    assert s.st.banner() == f"The model couldn't start: {error[:160]}"


def test_load_failure(ollama, status):
    ollama.on("POST", "/api/generate", Raw(500, b"model requires more system memory"))
    s = status(ollama.url)
    s.run()
    assert (s.st.state, s.st.error) == ("error", "model requires more system memory")


def test_unexpected_failure_is_shown(ollama, status, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(MS.requests, "post", boom)
    s = status(ollama.url)
    s.run()
    assert (s.st.state, s.st.error) == ("error", "kaputt")


@pytest.mark.qt
def test_ensure_runs_in_the_background_once(ollama, status, qtbot):
    s = status(ollama.url)
    s.st.ensure()
    qtbot.waitUntil(lambda: not s.st.busy(), timeout=5000)
    assert s.st.ready()
    n = len(ollama.requests)
    s.st.ensure()
    assert not s.st.busy() and len(ollama.requests) == n           # (ready a moment ago: nothing to do)
    s.st.ensure(force=True)
    qtbot.waitUntil(lambda: not s.st.busy(), timeout=5000)
    assert len(ollama.requests) > n


@pytest.mark.parametrize("state, banner, short", [
    ("unknown", None, ""),
    ("checking", "⏳ Model is starting up, please wait…", "AI starting"),
    ("starting_ollama", "⏳ Model is starting up, please wait…", "AI starting"),
    ("loading", "⏳ Model is starting up, please wait…", "AI starting"),
    ("ready", None, ""),
    ("downloading", f"⬇ Downloading the AI model ({MODEL})…", "AI not ready"),
])
def test_banner_and_short(status, state, banner, short):
    st = status("http://localhost:1").st
    st.state = state
    assert (st.banner(), st.short()) == (banner, short)


def test_short_while_downloading(status):
    st = status("http://localhost:1").st
    st.state, st.done_bytes, st.total_bytes = "downloading", 1, 4
    assert st.short() == "downloading the AI model 25%"


def test_error_banner_is_clipped(status):
    st = status("http://localhost:1").st
    st.state, st.error = "error", "e" * 300
    assert st.banner() == "The model couldn't start: " + "e" * 160
