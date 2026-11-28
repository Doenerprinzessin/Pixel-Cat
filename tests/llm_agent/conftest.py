"""Fixtures for the agent side of ``pixelcat.llm``: see agent_fakes.py for the fake desktop."""
from __future__ import annotations

from pathlib import Path

import pytest

from fakes.platform import FakeKnownFolders
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
        out[alias] = p
    return out


@pytest.fixture(autouse=True)
def folder_port(folders: dict[str, Path]) -> FakeKnownFolders:
    """The KnownFolders port the tools get: it knows ``folders`` (and ``folders["notes"]`` is set from it)."""
    port = FakeKnownFolders()
    known = dict(folders)
    port.answers("known_folder", known.get)
    folders["notes"] = paths.notes_dir(port)
    return port


@pytest.fixture(autouse=True)
def desk(monkeypatch: pytest.MonkeyPatch, clock: Clock, folder_port: FakeKnownFolders) -> FakeDesktop:
    d = FakeDesktop()
    d.install(monkeypatch, clock, folders=folder_port)
    return d


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def hooks(folder_port, desk: FakeDesktop) -> Hooks:  # type: ignore[no-untyped-def]
    h = Hooks()
    h.folders = folder_port
    h.platform = desk.platform
    return h


@pytest.fixture
def toolbox(cfg: Config, hooks: Hooks) -> ToolBox:
    return ToolBox(cfg, hooks.hooks(), platform=hooks.platform)


@pytest.fixture
def agent(cfg: Config, desk: FakeDesktop) -> DesktopAgent:
    return DesktopAgent(cfg, OWN_PID, platform=desk.platform)
