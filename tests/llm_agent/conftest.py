"""Fixtures for the agent side of ``pixelcat.llm``: see agent_fakes.py for the fake desktop."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_agent.agent_fakes import FOLDER_ALIASES, OWN_PID, Clock, FakeDesktop, Hooks
from pixelcat import paths
from pixelcat.llm.agent import DesktopAgent
from pixelcat.llm.tools import ToolBox
from pixelcat.settings import Config


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(autouse=True)
def folders(isolated_dirs: Path) -> dict[str, Path]:
    """The Windows known folders (Desktop, Documents ...) as fresh temp folders - never the real ones."""
    base = isolated_dirs / "known"
    out = {}
    for alias in FOLDER_ALIASES:
        p = base / alias.capitalize()
        p.mkdir(parents=True)
        paths._known_cache[alias] = p
        out[alias] = p
    out["notes"] = paths.notes_dir()
    return out


@pytest.fixture(autouse=True)
def desk(monkeypatch: pytest.MonkeyPatch, fake_dll: type, clock: Clock) -> FakeDesktop:
    d = FakeDesktop()
    d.install(monkeypatch, fake_dll, clock)
    return d


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def hooks() -> Hooks:
    return Hooks()


@pytest.fixture
def toolbox(cfg: Config, hooks: Hooks) -> ToolBox:
    return ToolBox(cfg, hooks.hooks())


@pytest.fixture
def agent(cfg: Config) -> DesktopAgent:
    return DesktopAgent(cfg, OWN_PID)
