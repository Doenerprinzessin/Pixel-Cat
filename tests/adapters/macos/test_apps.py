"""pixelcat.adapters.macos.apps: the .app index and its matching, ``open``, System Settings pages, the
editors and Ollama (fakes; live: read-only)."""
from __future__ import annotations

import plistlib
import time
from pathlib import Path

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, apps
from pixelcat.domain.apps import SETTINGS_PAGES


def _app(root: Path, file: str, **info: str) -> Path:
    bundle = root / f"{file}.app"
    (bundle / "Contents").mkdir(parents=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    return bundle


@pytest.fixture
def installed(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A fake /Applications with a few apps; Finder shows the German names of two of them."""
    root = tmp_path / "Applications"
    sysroot = tmp_path / "System" / "Applications"
    _app(root, "Visual Studio Code", CFBundleName="Code", CFBundleDisplayName="Code")
    _app(root, "Microsoft Word", CFBundleName="Microsoft Word")
    _app(root / "Microsoft Office Tools", "Microsoft AutoUpdate", CFBundleName="Microsoft AutoUpdate")
    _app(root, "Uninstall Foo", CFBundleName="Uninstall Foo")
    calc = _app(sysroot, "Calculator", CFBundleName="Calculator")
    _app(sysroot, "TextEdit", CFBundleName="TextEdit")
    _app(sysroot / "Utilities", "Activity Monitor", CFBundleName="Activity Monitor")
    (root / "Broken.app").mkdir()           # no Info.plist: still found by its file name
    german = {str(calc): "Rechner.app", str(sysroot / "Utilities" / "Activity Monitor.app"): "Aktivitätsanzeige"}
    fm = FakeModule(displayNameAtPath_=lambda p: german.get(p, Path(p).stem))
    monkeypatch.setattr(_objc, "FN", FakeModule(NSFileManager=FakeModule(defaultManager=lambda: fm)))
    monkeypatch.setattr(apps, "app_roots", lambda: [root, sysroot, sysroot / "Utilities", tmp_path / "nope"])
    monkeypatch.setattr(apps, "EXTRA_APPS", ())
    monkeypatch.setattr(apps, "_index", None)
    return tmp_path


@pytest.mark.parametrize("query, name, path", [
    ("calculator", "Rechner", "System/Applications/Calculator.app"),
    ("Rechner", "Rechner", "System/Applications/Calculator.app"),      # the name Finder shows
    ("calc", "Rechner", "System/Applications/Calculator.app"),         # starts with
    ("vs code", "Visual Studio Code", "Applications/Visual Studio Code.app"),   # alias
    ("code", "Visual Studio Code", "Applications/Visual Studio Code.app"),
    ("word", "Microsoft Word", "Applications/Microsoft Word.app"),     # alias
    ("autoupdate", "Microsoft AutoUpdate", "Applications/Microsoft Office Tools/Microsoft AutoUpdate.app"),
    ("task manager", "Aktivitätsanzeige", "System/Applications/Utilities/Activity Monitor.app"),
    ("notepad", "TextEdit", "System/Applications/TextEdit.app"),
    ("calculater", "Rechner", "System/Applications/Calculator.app"),   # close spelling
    ("broken", "Broken", "Applications/Broken.app"),
])
def test_find_shortcut(installed, query, name, path):  # type: ignore[no-untyped-def]
    assert apps.MacApps().find_shortcut(query) == (name, installed / path)


@pytest.mark.parametrize("query", ["", "  ", "uninstall foo", "zzzzqqq", "edge"])
def test_find_shortcut_misses(installed, query):  # type: ignore[no-untyped-def]
    assert apps.MacApps().find_shortcut(query) is None


def test_the_index_is_built_once_and_prefetched(installed, monkeypatch):  # type: ignore[no-untyped-def]
    builds = []
    real = apps.build_index
    monkeypatch.setattr(apps, "build_index", lambda: builds.append(1) or real())
    a = apps.MacApps()
    a.prefetch()
    for _ in range(100):
        if apps._index is not None:
            break
        time.sleep(0.02)
    a.find_shortcut("word")
    a.find_shortcut("calculator")
    a.prefetch()
    assert builds == [1]


def test_open_uses_usr_bin_open(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([apps.OPEN])
    a = apps.MacApps()
    a.open(Path("/Applications/Safari.app"))
    a.open("https://example.com")
    a.open("-weird")
    assert fake_run.calls[0] == [apps.OPEN, "/Applications/Safari.app"]
    assert fake_run.calls[1] == [apps.OPEN, "https://example.com"]
    assert fake_run.calls[2][1].startswith("/") and fake_run.calls[2][1].endswith("-weird")


def test_open_failures_are_oserrors_with_a_hint(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([apps.OPEN], returncode=1, stderr="The file /nope does not exist.\n")
    with pytest.raises(OSError, match=r"couldn't open /nope: The file /nope does not exist\. - check that it exists"):
        apps.MacApps().open("/nope")


def test_every_settings_page_has_a_pane(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([apps.OPEN])
    fake_run.answer(["/usr/bin/pmset", "-g", "batt"], "Now drawing from 'AC Power'\n")
    assert set(apps.SETTINGS_IDS) == set(SETTINGS_PAGES)
    a = apps.MacApps()
    a.open_settings("home")
    a.open_settings("sound")
    a.open_settings("mouse")
    opened = [c[1] for c in fake_run.calls if c[0] == apps.OPEN]
    assert opened == ["x-apple.systempreferences:", "x-apple.systempreferences:com.apple.Sound-Settings.extension",
                      "x-apple.systempreferences:com.apple.Mouse-Settings.extension"]
    with pytest.raises(OSError, match="no page"):
        a.open_settings("registry")


def test_mouse_settings_on_a_macbook_are_the_trackpad(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([apps.OPEN])
    fake_run.answer(["/usr/bin/pmset", "-g", "batt"], " -InternalBattery-0 (id=1)\t90%; charging\n")
    apps.MacApps().open_settings("mouse")
    assert fake_run.calls[-1] == [apps.OPEN, "x-apple.systempreferences:" + apps.TRACKPAD_ID]


def test_no_admin_on_macos():
    a = apps.MacApps()
    assert a.needs_admin("/Applications/X.app") is False
    assert a.run_as_admin("/Applications/X.app") == (False, apps.RUN_AS_ADMIN)
    with pytest.raises(OSError, match="administrator"):
        a.open_elevated("x", [])


class _Url:
    def __init__(self, path: str) -> None:
        self._path = path

    def path(self) -> str:
        return self._path


def _workspace(monkeypatch, found):  # type: ignore[no-untyped-def]
    ws = FakeModule(URLForApplicationWithBundleIdentifier_=lambda bid: found.get(bid))
    monkeypatch.setattr(_objc, "AK", FakeModule(NSWorkspace=FakeModule(sharedWorkspace=lambda: ws)))


def test_editor_commands(monkeypatch):  # type: ignore[no-untyped-def]
    _workspace(monkeypatch, {apps.VSCODE_ID: _Url("/Applications/Visual Studio Code.app")})
    a = apps.MacApps()
    assert a.editor_command("vscode") == [apps.OPEN, "-a", "/Applications/Visual Studio Code.app"]
    assert a.editor_command("notepad") == [apps.OPEN, "-e"]
    assert a.editor_command("emacs") is None
    _workspace(monkeypatch, {})
    assert a.editor_command("vscode") is None


def test_find_ollama_prefers_the_app(isolated_dirs, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    brew = tmp_path / "homebrew"
    monkeypatch.setattr(apps, "BIN_DIRS", (str(tmp_path / "nothing"), str(brew)))
    monkeypatch.setattr(apps.shutil, "which", lambda name: None)
    monkeypatch.setattr(apps, "OLLAMA_APP", tmp_path / "Applications" / "Ollama.app")
    a = apps.MacApps()
    assert a.find_ollama() is None
    brew.mkdir()
    (brew / "ollama").write_text("#!/bin/sh\n")
    (brew / "ollama").chmod(0o755)
    assert a.find_ollama() == brew / "ollama"
    monkeypatch.setattr(apps.shutil, "which", lambda name: "/somewhere/ollama")
    assert a.find_ollama() == Path("/somewhere/ollama")
    user_app = Path.home() / "Applications" / "Ollama.app"
    user_app.mkdir(parents=True)
    assert a.find_ollama() == user_app
    apps.OLLAMA_APP.mkdir(parents=True)
    assert a.find_ollama() == apps.OLLAMA_APP


def test_start_ollama(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    spawned = []
    monkeypatch.setattr(_objc, "spawn", lambda argv, cwd=None: spawned.append((argv, cwd)))
    fake_run.answer([apps.OPEN])
    a = apps.MacApps()
    a.start_ollama(Path("/Applications/Ollama.app"))
    a.start_ollama(Path("/opt/homebrew/bin/ollama"))
    assert fake_run.calls == [[apps.OPEN, "-g", "-a", "/Applications/Ollama.app"]]
    assert spawned == [(["/opt/homebrew/bin/ollama", "serve"], "/opt/homebrew/bin")]


def test_start_ollama_gives_up_quietly(monkeypatch):  # type: ignore[no-untyped-def]
    def fail(argv, cwd=None):  # type: ignore[no-untyped-def]
        raise OSError("not executable")
    monkeypatch.setattr(_objc, "spawn", fail)
    apps.MacApps().start_ollama(Path("/x/ollama"))


# ------------------------------------------------------------------ live (read-only)
@pytest.mark.macos
def test_live_the_index_finds_calculator(monkeypatch):
    monkeypatch.setattr(apps, "_index", None)
    monkeypatch.setattr(apps.Path, "home", classmethod(lambda cls: Path("/var/empty")))
    hit = apps.MacApps().find_shortcut("calculator")
    assert hit is not None and hit[1] == Path("/System/Applications/Calculator.app")


@pytest.mark.macos
def test_live_every_settings_pane_exists():
    ids = set()
    for ext in Path("/System/Library/ExtensionKit/Extensions").glob("*.appex"):
        try:
            with open(ext / "Contents" / "Info.plist", "rb") as fh:
                ids.add(plistlib.load(fh).get("CFBundleIdentifier"))
        except Exception:  # noqa: BLE001 - a few system plists aren't valid XML
            continue
    missing = {page: pane for page, pane in {**apps.SETTINGS_IDS, "trackpad": apps.TRACKPAD_ID}.items()
               if pane and pane not in ids}
    assert not missing
