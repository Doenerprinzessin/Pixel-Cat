"""main.py: which part of the program the command line starts (without starting it)."""
from __future__ import annotations

import sys
import types

import pytest

from fakes.platform import FakePlatform
from pixelcat import bootstrap, logsetup
from pixelcat.adapters.windows import installer


@pytest.fixture
def app_main(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """main.main() with logging, DPI awareness and the installer entry points faked."""
    import main

    calls: list = []
    platform = FakePlatform()
    monkeypatch.setattr(logsetup, "setup_logging", lambda debug=False: calls.append(("logging", debug)))
    monkeypatch.setattr(bootstrap, "build_platform", lambda *a, **k: platform)
    platform.display.answers("set_dpi_awareness", lambda: calls.append(("dpi",)))
    platform.system.answers("message_box", lambda text, *a, **k: calls.append(("message", text)))
    platform.system.returns("acquire_single_instance", False)
    platform.installer.answers("is_setup_name", installer.is_setup_name)     # the real name check
    platform.installer.answers("run_quiet", lambda args: calls.append(("quiet", args)) or 7)
    platform.installer.answers("run_window", lambda mode: calls.append(("window", mode)) or 8)
    monkeypatch.delattr(sys, "frozen", raising=False)

    def run(*argv: str, frozen: bool = False, exe: str = "PixelCat.exe") -> int:
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])
        monkeypatch.setattr(sys, "executable", exe)
        if frozen:
            monkeypatch.setattr(sys, "frozen", True, raising=False)
        return main.main()

    return types.SimpleNamespace(run=run, calls=calls, platform=platform)


def test_installer_needs_the_built_exe(app_main, capsys: pytest.CaptureFixture) -> None:  # type: ignore[no-untyped-def]
    assert app_main.run("--install") == 1
    assert capsys.readouterr().out == \
        "The installer works with the built exe: run  python build.py  and then dist\\PixelCat-Setup.exe\n"
    assert app_main.calls == [("logging", False), ("dpi",)]


@pytest.mark.parametrize("argv", [["--install"], ["--uninstall", "--quiet"]])
def test_on_a_mac_the_installer_says_there_is_none_yet(monkeypatch, capsys, argv: list[str]) -> None:  # type: ignore[no-untyped-def]
    import main
    from pixelcat.adapters.macos import storage
    from pixelcat.adapters.macos.profile import MAC

    platform = FakePlatform(profile=MAC, installer=storage.MacInstaller())
    monkeypatch.setattr(logsetup, "setup_logging", lambda debug=False: None)
    monkeypatch.setattr(bootstrap, "build_platform", lambda *a, **k: platform)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])
    assert main.main() == 1
    assert capsys.readouterr().out == storage.NO_INSTALLER + "\n"
    assert platform.system.calls == []


def test_quiet_install_options_reach_the_installer(app_main) -> None:  # type: ignore[no-untyped-def]
    assert app_main.run("--install", "--quiet", "--no-desktop-shortcut", "--with-ai", "--launch", frozen=True) == 7
    ((kind, args),) = [c for c in app_main.calls if c[0] == "quiet"]
    assert (args.install, args.quiet, args.no_desktop_shortcut, args.with_ai, args.launch, args.uninstall) == \
        (True, True, True, True, True, False)


def test_quiet_uninstall(app_main) -> None:  # type: ignore[no-untyped-def]
    assert app_main.run("--uninstall", "--quiet", "--delete-data", "--debug", frozen=True) == 7
    args = app_main.calls[-1][1]
    assert args.uninstall and args.delete_data
    assert app_main.calls[0] == ("logging", True)


@pytest.mark.parametrize("argv, exe, mode", [
    (["--uninstall"], "PixelCat.exe", "uninstall"),
    (["--install"], "PixelCat.exe", "install"),
    ([], r"C:\Users\me\Downloads\PixelCat-Setup (2).exe", "install"),
])
def test_installer_window(app_main, argv: list[str], exe: str, mode: str) -> None:  # type: ignore[no-untyped-def]
    assert app_main.run(*argv, frozen=True, exe=exe) == 8
    assert app_main.calls[-1] == ("window", mode)
    assert app_main.platform.system.calls == []     # the installer never takes the single-instance guard


def test_a_second_cat_only_says_so(app_main) -> None:  # type: ignore[no-untyped-def]
    assert app_main.run() == 0
    assert app_main.calls[-1][0] == "message" and "already running" in app_main.calls[-1][1]


# ---- what main loads before Qt (a fresh interpreter, the real Windows adapter; the test shim off Windows) ----------

# main sets the DPI awareness, runs the quiet installer and lets a second copy exit before Qt or the heavy
# desktop modules are loaded (Qt would otherwise pick its own DPI awareness first).
_STARTUP_PROBE = """
import json, sys
if sys.platform != "win32":
    import winshim
    winshim.install()
from pixelcat import bootstrap, logsetup
case = sys.argv[1]
if case == "dpi":
    platform = bootstrap.build_platform("win32")
    platform.display.set_dpi_awareness()
    rc = None
else:
    import main
    from pixelcat.adapters.windows import installer, system, win32   # (main loads the installer anyway)
    build = bootstrap.build_platform
    bootstrap.build_platform = lambda *a, **k: build("win32")
    logsetup.setup_logging = lambda debug=False: None
    installer.run_quiet = lambda args: 7                     # (the real one would install)
    system.acquire_single_instance = lambda: False           # another cat runs
    win32.message_box = lambda text, title="Pixel Cat": None
    sys.frozen = True
    sys.argv = ["main.py"] + (["--install", "--quiet"] if case == "quiet" else [])
    rc = main.main()
print("@@probe" + json.dumps({"rc": rc, "qt": sorted(m for m in sys.modules if m.split(".")[0] == "PySide6"),
                              "adapters": sorted(m.rsplit(".", 1)[1] for m in sys.modules
                                                 if m.startswith("pixelcat.adapters.windows."))}))
"""


def _startup(case: str) -> dict:
    import json
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "tests")])}
    res = subprocess.run([sys.executable, "-c", _STARTUP_PROBE, case], cwd=root, capture_output=True, text=True,
                         timeout=120, env=env)
    lines = [line for line in res.stdout.splitlines() if line.startswith("@@probe")]
    assert lines, res.stderr
    return json.loads(lines[-1][len("@@probe"):])


@pytest.mark.parametrize("case, rc, allowed", [
    ("dpi", None, {"platform", "system", "win32"}),
    ("quiet", 7, {"platform", "system", "win32", "installer", "elevation", "known_folders"}),
    ("second", 0, {"platform", "system", "win32", "installer", "elevation", "known_folders"}),
], ids=["dpi-awareness", "quiet-install", "second-instance"])
def test_main_does_not_load_qt_or_the_desktop_modules_before_it_needs_them(case: str, rc, allowed: set) -> None:  # type: ignore[no-untyped-def]
    res = _startup(case)
    assert res["rc"] == rc
    assert res["qt"] == []
    assert set(res["adapters"]) <= allowed, sorted(set(res["adapters"]) - allowed)
