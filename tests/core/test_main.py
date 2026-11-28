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
    assert "python build.py" in capsys.readouterr().out
    assert app_main.calls == [("logging", False), ("dpi",)]


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
