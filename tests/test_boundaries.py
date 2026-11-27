"""Import boundaries: the domain types and the cat/game core stay free of the platform adapters.

Static checks read every import (including the lazy ones inside functions) with ``ast``; the runtime
check imports the core in a fresh interpreter without the Windows shim and looks at ``sys.modules``.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "pixelcat"

# The platform-independent core: it may use pixelcat.domain, never pixelcat.adapters (nor the old pixelcat.desktop).
CORE = ["engine", "games", "sprites", "domain", "settings.py", "progress.py"]
# Qt glue that still calls Win32 directly; a later slice of PR 2 puts these calls behind desktop ports.
CORE_EXCEPTIONS = {"games/qt_host.py"}


def _module_name(path: Path, root: Path = ROOT) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, root: Path = ROOT) -> set[str]:
    """Absolute names of every module imported anywhere in ``path`` (relative imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path, root).split(".")
    if path.name != "__init__.py":
        package.pop()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else []
            mod = ".".join(base + ([node.module] if node.module else []))
            if node.module:
                found.add(mod)
            # "from . import x" / "from pkg import x" may name submodules
            found.update(f"{mod}.{alias.name}" for alias in node.names)
    return found


def _files(*entries: str) -> list[Path]:
    out: list[Path] = []
    for entry in entries:
        p = PKG / entry
        out.extend(sorted(p.rglob("*.py")) if p.is_dir() else [p])
    return out


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix()


def _is(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


@pytest.mark.parametrize("path", _files("domain"), ids=_rel)
def test_domain_imports_only_stdlib_and_itself(path):
    bad = sorted(name for name in _imports(path)
                 if (not _is(name, "pixelcat.domain") and name.split(".")[0] not in sys.stdlib_module_names)
                 or name.split(".")[0] in {"ctypes", "winreg", "msvcrt", "_winapi"})
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}"


@pytest.mark.parametrize("path", [p for p in _files(*CORE) if _rel(p) not in CORE_EXCEPTIONS], ids=_rel)
def test_core_does_not_import_adapters(path):
    bad = sorted(name for name in _imports(path) if _is(name, "pixelcat.adapters") or _is(name, "pixelcat.desktop"))
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}; use pixelcat.domain (or a port) instead"


@pytest.mark.parametrize("path", _files("ports"), ids=_rel)
def test_ports_import_only_stdlib_and_the_domain(path):
    # Qt only for type hints (under TYPE_CHECKING: see test_ports_load_without_qt_ctypes_or_adapters)
    bad = sorted(name for name in _imports(path)
                 if not (_is(name, "pixelcat.domain") or _is(name, "pixelcat.ports") or _is(name, "PySide6.QtGui")
                         or name.split(".")[0] in sys.stdlib_module_names)
                 or name.split(".")[0] in {"ctypes", "winreg", "msvcrt", "_winapi"})
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}"


def test_the_old_desktop_package_is_gone():
    # a stale __pycache__ folder from an old checkout may stay behind; source files may not
    left = sorted(p.relative_to(PKG).as_posix() for p in (PKG / "desktop").rglob("*.py"))
    assert not left, f"pixelcat/desktop is now pixelcat/adapters/windows: don't add it back ({left})"


def test_core_exceptions_still_exist():
    for rel in CORE_EXCEPTIONS:
        assert (PKG / rel).is_file(), f"{rel} is gone: drop it from CORE_EXCEPTIONS"


def test_import_scanner_resolves_relative_and_lazy_imports(tmp_path):
    pkg = tmp_path / "pixelcat" / "engine"
    pkg.mkdir(parents=True)
    src = pkg / "x.py"
    src.write_text("from ..adapters.windows.windows import WinInfo\ndef f():\n    from ..adapters.windows import win32\n",
                   encoding="utf-8")
    assert {"pixelcat.adapters.windows.windows", "pixelcat.adapters.windows",
            "pixelcat.adapters.windows.win32"} <= _imports(src, tmp_path)


def _loaded_after(code: str) -> set[str]:
    """Modules loaded by ``code`` in a fresh interpreter (no conftest, no Windows shim)."""
    probe = textwrap.dedent(code) + "\nimport sys\nprint('\\n'.join(sys.modules))\n"
    res = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(ROOT), "QT_QPA_PLATFORM": "offscreen"})
    assert res.returncode == 0, res.stderr
    return set(res.stdout.split())


def test_domain_loads_without_ctypes_qt_or_adapters():
    loaded = _loaded_after("import pixelcat.domain")
    assert not {m for m in loaded if _is(m, "pixelcat.adapters") or _is(m, "PySide6") or _is(m, "ctypes")}


def test_cat_and_game_logic_load_without_adapters():
    loaded = _loaded_after("""
        import pkgutil, importlib
        import pixelcat.engine, pixelcat.games
        for pkg in (pixelcat.engine, pixelcat.games):
            for m in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
                if m.name != "pixelcat.games.qt_host":
                    importlib.import_module(m.name)
    """)
    assert "pixelcat.games.mice" in loaded and "pixelcat.engine.brain" in loaded
    assert not {m for m in loaded if _is(m, "pixelcat.adapters")}


def test_ports_load_without_qt_ctypes_or_adapters():
    loaded = _loaded_after("import pixelcat.ports")
    assert "pixelcat.ports.storage" in loaded
    assert not {m for m in loaded if _is(m, "pixelcat.adapters") or _is(m, "PySide6") or _is(m, "ctypes")}
