"""Import boundaries: Win32 stays in the Windows adapter, Qt stays in the Qt layer, the core stays off the adapters.

The layout (ports and adapters):

* ``pixelcat/domain``: platform-free types: the standard library only.
* ``pixelcat/ports``: the interfaces the core talks to: the standard library and the domain (Qt only for type hints).
* ``pixelcat/adapters/windows``: all Win32 code (ctypes, winreg, msvcrt, _winapi, os.startfile ...).
* ``pixelcat/adapters/null``: does nothing, loads anywhere: no Win32.
* ``pixelcat/adapters/llm``: the AI model providers and their clients and sign-ins: no Win32, no Qt (they run on
  worker threads).
* ``pixelcat/bootstrap.py``: the composition root, the only one that picks an adapter.
* the core: everything else under ``pixelcat/``.  It gets its ports passed in and never imports an adapter.

Static checks read every import with ``ast``: the lazy ones inside functions, relative ones, the ones under
``if TYPE_CHECKING:`` (kept apart: they never run) and dynamic ones (``importlib.import_module("...")``,
``__import__("...")``).  Runtime checks import each core module on its own in a fresh interpreter, without the
Windows test shim, and record which module first imported each Win32 or Qt module (and so do the model providers').
"""
from __future__ import annotations

import ast
import json
import os
import pkgutil
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import pytest

import pixelcat

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "pixelcat"

# Win32 lives in pixelcat/adapters/windows only.  (os.startfile is checked too; ctypes.wintypes is part of ctypes.)
WIN32 = {"ctypes", "_ctypes", "winreg", "msvcrt", "_winapi", "nt", "winsound"}
QT = {"PySide6", "shiboken6", "shibokensupport"}   # shibokensupport: embedded in shiboken6
# Where Qt belongs (core files or packages).  Every other core module may use Qt in type hints only.
QT_ALLOWED = {
    "app.py": "the application: QApplication, the windows, timers and the tray",
    "ui": "the widgets: overlay, ball, menu, chat, dialogs, tray, theme, screen geometry",
    "games/qt_host.py": "the minigames' Qt window (the games themselves are plain Python)",
    "imaging.py": "picture helpers for the chat (QImage in, PNG bytes out)",
    "sprites/library.py": "turns the plain sprite rasters into QPixmaps",
    "llm/agent.py": "compares screen pictures (QImage) for the desktop agent",
    "llm/watch.py": "the watches report back with Qt signals",
    "llm/qt_chat.py": "the Qt bridge: runs a chat job in a QThread and turns its events into signals",
}
# Core modules that don't import Qt themselves but load it through a module in QT_ALLOWED.
QT_VIA = {
    "llm/tools.py": "uses llm/agent.py",
}
# Core files allowed to import a module whose name is only known at run time (path: reason).  None so far.
DYNAMIC_IMPORTS_ALLOWED: dict[str, str] = {}


@dataclass
class _Scan:
    runtime: set[str] = field(default_factory=set)      # imports that run (top level, lazy, dynamic with a literal)
    type_only: set[str] = field(default_factory=set)    # imports under "if TYPE_CHECKING:"
    dynamic: list[int] = field(default_factory=list)    # lines of dynamic imports without a literal name
    startfile: list[int] = field(default_factory=list)  # lines that use os.startfile

    @property
    def imports(self) -> set[str]:
        return self.runtime | self.type_only


def _module_name(path: Path, root: Path = ROOT) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
        (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _literal(node: ast.expr | None):  # type: ignore[no-untyped-def]
    try:
        return ast.literal_eval(node) if node is not None else None
    except ValueError:
        return None


def _scan(path: Path, root: Path = ROOT) -> _Scan:
    """Absolute names of every module ``path`` imports (relative and dynamic imports resolved)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(path, root).split(".")
    if path.name != "__init__.py":
        package.pop()
    os_names = {"os", "nt"} | {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                               for a in n.names if a.name in ("os", "nt")}
    scan = _Scan()

    def absolute(level: int, module: str | None) -> str:
        base = package[:len(package) - level + 1] if level else []
        return ".".join(base + ([module] if module else []))

    def dynamic(node: ast.Call, found: set[str]) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name == "getattr" and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) \
                and node.args[0].id in os_names and _literal(node.args[1]) == "startfile":
            scan.startfile.append(node.lineno)
        if name not in ("import_module", "__import__"):
            return
        kw = {k.arg: k.value for k in node.keywords}
        target = _literal(node.args[0] if node.args else kw.get("name"))
        if not isinstance(target, str):
            scan.dynamic.append(node.lineno)
            return
        if name == "import_module":
            level = len(target) - len(target.lstrip("."))
            mod = absolute(level, target.lstrip(".") or None)
            fromlist = []
        else:
            level = _literal(node.args[4] if len(node.args) > 4 else kw.get("level")) or 0
            mod = absolute(level, target)
            fromlist = _literal(node.args[3] if len(node.args) > 3 else kw.get("fromlist")) or []
        found.add(mod)
        found.update(f"{mod}.{x}" for x in fromlist if isinstance(x, str))

    def visit(node: ast.AST, type_only: bool) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                visit(child, True)
            for child in node.orelse:
                visit(child, type_only)
            return
        found = scan.type_only if type_only else scan.runtime
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = absolute(node.level, node.module)
            if node.module:
                found.add(mod)
            # "from . import x" / "from pkg import x" may name submodules
            found.update(f"{mod}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            dynamic(node, found)
        elif isinstance(node, ast.Attribute) and node.attr == "startfile" and isinstance(node.value, ast.Name) \
                and node.value.id in os_names:
            scan.startfile.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child, type_only)

    visit(tree, False)
    if "os.startfile" in scan.imports or "nt.startfile" in scan.imports:   # from os import startfile
        scan.startfile.append(0)
    return scan


def _rel(path: Path) -> str:
    return path.relative_to(PKG).as_posix()


def _is(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _top(name: str) -> str:
    return name.split(".")[0]


def _not_for_the_llm_adapter(name: str) -> bool:
    """Another adapter (any ``pixelcat.adapters.*`` but the llm one; the package itself is just its parent) or the
    wiring: the model providers load neither, whether they import it at the top or in a function."""
    return ((_is(name, "pixelcat.adapters") and name != "pixelcat.adapters" and not _is(name, "pixelcat.adapters.llm"))
            or _is(name, "pixelcat.bootstrap"))


def _stdlib(name: str) -> bool:
    return _top(name) in sys.stdlib_module_names and _top(name) not in WIN32


def _covered(rel: str, entries) -> bool:  # type: ignore[no-untyped-def]
    return any(rel == e or rel.startswith(e + "/") for e in entries)


ALL_FILES = sorted(PKG.rglob("*.py"))
WINDOWS_ADAPTER = [p for p in ALL_FILES if _covered(_rel(p), {"adapters/windows"})]
NON_WINDOWS = [p for p in ALL_FILES if p not in WINDOWS_ADAPTER]
CORE = [p for p in ALL_FILES if not _covered(_rel(p), {"adapters", "bootstrap.py"})]
CORE_QT_FREE = [p for p in CORE if not _covered(_rel(p), QT_ALLOWED)]
DOMAIN = [p for p in CORE if _covered(_rel(p), {"domain"})]
PORTS = [p for p in CORE if _covered(_rel(p), {"ports"})]
NULL_ADAPTER = [p for p in ALL_FILES if _covered(_rel(p), {"adapters/null"})]
LLM_ADAPTER = [p for p in ALL_FILES if _covered(_rel(p), {"adapters/llm"})]
# Every core module, found the way Python finds them (recursively, through the subpackages).
CORE_MODULES = ["pixelcat"] + sorted(
    m.name for m in pkgutil.walk_packages(pixelcat.__path__, "pixelcat.")
    if not (_is(m.name, "pixelcat.adapters") or _is(m.name, "pixelcat.bootstrap")))
QT_MODULES = {_module_name(p) for p in CORE if _covered(_rel(p), QT_ALLOWED)}
QT_VIA_MODULES = {_module_name(PKG / rel) for rel in QT_VIA}
LLM_ADAPTER_MODULES = sorted(_module_name(p) for p in LLM_ADAPTER)


# ---- the lists can't go stale ------------------------------------------------------------------------------------


def test_the_file_lists_cover_the_whole_core():
    assert WINDOWS_ADAPTER and NULL_ADAPTER and DOMAIN and PORTS and CORE_QT_FREE
    assert {_module_name(p) for p in CORE} == set(CORE_MODULES), "a folder in pixelcat/ lacks its __init__.py?"
    for name in ("pixelcat.settings", "pixelcat.progress", "pixelcat.paths", "pixelcat.persistence",
                 "pixelcat.savecrypt", "pixelcat.chats", "pixelcat.llm.tools", "pixelcat.sprites.raster",
                 "pixelcat.games.mice", "pixelcat.engine.brain", "pixelcat.domain.windows", "pixelcat.ports.desktop"):
        assert name in CORE_MODULES


@pytest.mark.parametrize("entry", sorted(QT_ALLOWED))
def test_qt_allowed_entries_exist_and_use_qt(entry):
    files = [p for p in CORE if _covered(_rel(p), {entry})]
    assert files, f"pixelcat/{entry} is gone: drop it from QT_ALLOWED"
    assert any(_top(name) in QT for p in files for name in _scan(p).runtime), \
        f"pixelcat/{entry} no longer uses Qt: drop it from QT_ALLOWED"


@pytest.mark.parametrize("rel", sorted(QT_VIA))
def test_qt_via_entries_exist_and_import_a_qt_module(rel):
    assert (PKG / rel).is_file(), f"pixelcat/{rel} is gone: drop it from QT_VIA"
    assert rel not in QT_ALLOWED
    assert _scan(PKG / rel).runtime & (QT_MODULES | QT_VIA_MODULES), \
        f"pixelcat/{rel} imports no Qt module of the core any more: drop it from QT_VIA"


def test_dynamic_import_entries_exist():
    for rel in DYNAMIC_IMPORTS_ALLOWED:
        assert (PKG / rel).is_file() and _scan(PKG / rel).dynamic, f"drop pixelcat/{rel} from DYNAMIC_IMPORTS_ALLOWED"


def test_the_old_desktop_package_is_gone():
    # a stale __pycache__ folder from an old checkout may stay behind; source files may not
    left = sorted(p.relative_to(PKG).as_posix() for p in (PKG / "desktop").rglob("*.py"))
    assert not left, f"pixelcat/desktop is now pixelcat/adapters/windows: don't add it back ({left})"


# ---- static rules ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", NON_WINDOWS, ids=_rel)
def test_only_the_windows_adapter_uses_win32(path):
    scan = _scan(path)
    bad = sorted(name for name in scan.imports if _top(name) in WIN32)
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}; Win32 belongs in pixelcat/adapters/windows (behind a port)"
    assert not scan.startfile, f"pixelcat/{_rel(path)} uses os.startfile (lines {scan.startfile}); use a port"


@pytest.mark.parametrize("path", CORE, ids=_rel)
def test_core_does_not_import_adapters(path):
    bad = sorted(name for name in _scan(path).imports
                 if _is(name, "pixelcat.adapters") or _is(name, "pixelcat.bootstrap") or _is(name, "pixelcat.desktop"))
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}; use pixelcat.domain or a port (pixelcat.ports) instead"


@pytest.mark.parametrize("path", CORE_QT_FREE, ids=_rel)
def test_qt_stays_in_the_qt_layer(path):
    bad = sorted(name for name in _scan(path).runtime if _top(name) in QT)
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}; only the modules in QT_ALLOWED use Qt (type hints aside)"


@pytest.mark.parametrize("path", CORE, ids=_rel)
def test_core_dynamic_imports_name_their_module(path):
    scan = _scan(path)
    if _rel(path) not in DYNAMIC_IMPORTS_ALLOWED:
        assert not scan.dynamic, (f"pixelcat/{_rel(path)} imports a module named at run time (lines {scan.dynamic}); "
                                  "the boundary checks can't see it: add it to DYNAMIC_IMPORTS_ALLOWED with a reason")


@pytest.mark.parametrize("path", DOMAIN, ids=_rel)
def test_domain_imports_only_stdlib_and_itself(path):
    bad = sorted(name for name in _scan(path).imports if not (_is(name, "pixelcat.domain") or _stdlib(name)))
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}"


@pytest.mark.parametrize("path", PORTS, ids=_rel)
def test_ports_import_only_stdlib_and_the_domain(path):
    scan = _scan(path)
    bad = sorted(name for name in scan.runtime
                 if not (_is(name, "pixelcat.domain") or _is(name, "pixelcat.ports") or _stdlib(name)))
    bad += sorted(name for name in scan.type_only
                  if not (_is(name, "pixelcat.domain") or _is(name, "pixelcat.ports") or _stdlib(name)
                          or _top(name) in QT))
    assert not bad, f"pixelcat/{_rel(path)} imports {bad} (Qt only under 'if TYPE_CHECKING:')"


@pytest.mark.parametrize("path", NULL_ADAPTER, ids=_rel)
def test_null_adapter_imports_only_stdlib_domain_ports_and_qt(path):
    bad = sorted(name for name in _scan(path).imports
                 if not (_is(name, "pixelcat.domain") or _is(name, "pixelcat.ports")
                         or _is(name, "pixelcat.adapters.null") or _stdlib(name) or _top(name) in QT))
    assert not bad, f"pixelcat/{_rel(path)} imports {bad}; the null adapter must load on any platform"


def test_the_llm_adapter_exists():
    assert LLM_ADAPTER


@pytest.mark.parametrize("path", LLM_ADAPTER, ids=_rel)
def test_llm_adapter_is_qt_free(path):
    bad = sorted(name for name in _scan(path).imports
                 if _top(name) in QT or name in QT_MODULES | QT_VIA_MODULES or _not_for_the_llm_adapter(name))
    assert not bad, (f"pixelcat/{_rel(path)} imports {bad}; the model providers stay Qt-free (the app bridges to Qt) "
                     "and use no other adapter")


@pytest.mark.parametrize("path", LLM_ADAPTER, ids=_rel)
def test_llm_adapter_dynamic_imports_name_their_module(path):
    scan = _scan(path)
    assert not scan.dynamic, f"pixelcat/{_rel(path)} imports a module named at run time (lines {scan.dynamic})"


def test_the_llm_port_imports_only_stdlib():
    bad = sorted(name for name in _scan(PKG / "ports" / "llm.py").imports if not _stdlib(name))
    assert not bad, f"pixelcat/ports/llm.py imports {bad}"


def test_import_scanner_sees_relative_lazy_typing_and_dynamic_imports(tmp_path):
    pkg = tmp_path / "pixelcat" / "engine"
    pkg.mkdir(parents=True)
    src = pkg / "x.py"
    src.write_text(textwrap.dedent("""
        import importlib, os as _os
        from typing import TYPE_CHECKING
        from ..adapters.windows.windows import WinInfo
        from .. import adapters
        if TYPE_CHECKING:
            from PySide6.QtGui import QImage
        def f(name):
            from ..adapters.windows import win32
            importlib.import_module("ctypes.wintypes")
            importlib.import_module("..ui.chat", __package__)
            __import__("winreg")
            __import__("adapters.null", globals(), None, ["thing"], 2)
            importlib.import_module(name)
            _os.startfile("x")
            getattr(_os, "startfile")("x")
    """), encoding="utf-8")
    scan = _scan(src, tmp_path)
    assert {"pixelcat.adapters.windows.windows", "pixelcat.adapters", "pixelcat.adapters.windows.win32",
            "ctypes.wintypes", "pixelcat.ui.chat", "winreg", "pixelcat.adapters.null.thing"} <= scan.runtime
    assert scan.type_only == {"PySide6.QtGui", "PySide6.QtGui.QImage"}
    assert scan.dynamic == [14] and scan.startfile == [15, 16]
    (pkg / "y.py").write_text("from os import startfile\n", encoding="utf-8")
    assert _scan(pkg / "y.py", tmp_path).startfile


def test_the_llm_adapter_check_sees_another_adapter_imported_in_a_function(tmp_path):
    pkg = tmp_path / "pixelcat" / "adapters" / "llm"
    pkg.mkdir(parents=True)
    src = pkg / "z.py"
    src.write_text(textwrap.dedent("""
        from . import base
        from .. import llm
        def f():
            import pixelcat.adapters.macos
            from .. import windows
            from ... import bootstrap
            from ...adapters.llm import signin
    """), encoding="utf-8")
    bad = {name for name in _scan(src, tmp_path).imports if _not_for_the_llm_adapter(name)}
    assert bad == {"pixelcat.adapters.macos", "pixelcat.adapters.windows", "pixelcat.bootstrap"}


# ---- runtime: what importing a module really loads ------------------------------------------------------------------

# Runs in a fresh interpreter (no conftest, so no Windows shim): imports one module and records, for every Win32,
# Qt or adapter module loaded on the way, the module whose code asked for it.  The importer matters: on Windows the
# interpreter loads winreg at startup (importlib._bootstrap_external) and the standard library loads some of these
# itself (subprocess and shutil load _winapi and msvcrt, asyncio loads _winapi...), and that's fine; a pixelcat
# module asking for them is not.
_PROBE = """
import json, sys
WATCHED = ("pixelcat.adapters", "pixelcat.bootstrap", "pixelcat.desktop", "PySide6", "shiboken6", "shibokensupport",
           "ctypes", "_ctypes", "winreg", "msvcrt", "_winapi", "winsound")
events = []

def importer():
    frame = sys._getframe(2)
    while frame is not None:
        name = frame.f_globals.get("__name__", "?")
        if not (name == "importlib" or name.startswith(("importlib.", "_frozen_importlib"))):
            return name
        frame = frame.f_back
    return "?"

class Recorder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if any(name == w or name.startswith(w + ".") for w in WATCHED):
            events.append((name, importer()))
        return None

sys.meta_path.insert(0, Recorder)
error = None
try:
    __import__(sys.argv[1])
except BaseException as exc:
    error = f"{type(exc).__name__}: {exc}"
loaded = sorted(m for m in sys.modules if any(m == w or m.startswith(w + ".") for w in WATCHED))
print("@@probe" + json.dumps({"events": events, "loaded": loaded, "error": error}))
"""


def _probe(name: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "QT_QPA_PLATFORM": "offscreen"}
    res = subprocess.run([sys.executable, "-c", _PROBE, name], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env=env)
    lines = [line for line in res.stdout.splitlines() if line.startswith("@@probe")]
    assert lines, f"probing {name} failed:\n{res.stderr}"
    return json.loads(lines[-1][len("@@probe"):])


@cache
def _probes() -> dict[str, dict]:
    names = CORE_MODULES + ["pixelcat.adapters.null"] + LLM_ADAPTER_MODULES
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        return dict(zip(names, pool.map(_probe, names)))


@pytest.mark.parametrize("name", CORE_MODULES)
def test_core_module_loads_without_win32_or_adapters(name):
    res = _probes()[name]
    adapters = sorted({m for m in res["loaded"] if _is(m, "pixelcat")}
                      | {f"{m} (by {by})" for m, by in res["events"] if _is(m, "pixelcat")})
    assert not adapters, f"importing {name} loads {adapters}"
    win32 = sorted(f"{m} (by {by})" for m, by in res["events"] if _top(m) in WIN32 and _is(by, "pixelcat"))
    assert not win32, f"importing {name} loads {win32}"
    qt = sorted(f"{m} (by {by})" for m, by in res["events"]
                if _top(m) in QT and not (_top(by) in QT or by in QT_MODULES))
    assert not qt, f"importing {name} loads {qt}; only the modules in QT_ALLOWED may load Qt"
    assert res["error"] is None, f"importing {name} without the Windows shim fails: {res['error']}"
    uses_qt = any(_top(m) in QT for m in res["loaded"])
    if name in QT_VIA_MODULES:
        assert uses_qt, f"{name} no longer loads Qt: drop it from QT_VIA"
    elif name not in QT_MODULES:
        assert not uses_qt, f"importing {name} loads Qt; it is in neither QT_ALLOWED nor QT_VIA"


def test_null_adapter_loads_without_win32_qt_or_the_windows_adapter():
    res = _probes()["pixelcat.adapters.null"]
    assert res["error"] is None, res["error"]
    assert not [m for m in res["loaded"] if _is(m, "pixelcat.adapters.windows") or _top(m) in QT]
    assert not [m for m, by in res["events"] if _top(m) in WIN32 and _is(by, "pixelcat")]


@pytest.mark.parametrize("name", LLM_ADAPTER_MODULES)
def test_llm_adapter_loads_without_win32_qt_or_another_adapter(name):
    res = _probes()[name]
    assert res["error"] is None, res["error"]
    assert not [m for m in res["loaded"] if _top(m) in QT or _not_for_the_llm_adapter(m)]
    assert not [m for m, by in res["events"] if _top(m) in WIN32 and _is(by, "pixelcat")]
