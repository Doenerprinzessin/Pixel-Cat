"""Real-Windows checks for the agent tools (read-only: the user's known folders).

The autouse ``folders`` fixture is replaced here so the tools see the user's REAL known folders;
nothing in these tests writes, launches or clicks anything.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pixelcat import paths
from pixelcat.llm.tools import ToolBox

pytestmark = pytest.mark.windows


@pytest.fixture(autouse=True)
def folders(isolated_dirs: Path, real_profile) -> None:  # type: ignore[no-untyped-def]
    """Override: leave the known-folder cache empty so Windows itself is asked."""
    paths._known_cache.clear()


def test_allowed_roots_are_the_real_known_folders(toolbox: ToolBox) -> None:
    roots = toolbox._allowed_roots()
    assert len(roots) == len(toolbox.cfg.allowed_roots)
    for alias in ("desktop", "documents", "downloads"):
        real = paths.known_folder(alias)
        assert real is not None and real.is_absolute()
        assert real.resolve() in roots


def test_alias_paths_resolve_into_the_real_desktop(toolbox: ToolBox) -> None:
    name = "pixelcat-test-does-not-exist-7f3a.txt"
    p = toolbox._resolve_file(f"desktop/{name}", must_exist=False)
    assert p == (paths.known_folder("desktop") / name).resolve()
    assert not p.exists()


def test_list_files_reads_a_real_folder(toolbox: ToolBox) -> None:
    if not paths.known_folder("documents").is_dir():
        pytest.skip("no Documents folder on this machine")
    spec, a = toolbox.prepare("list_files", {"folder": "documents"})
    out = toolbox.run(spec, a)
    assert out.startswith(f"{paths.known_folder('documents').resolve()}: ")

