"""pixelcat.adapters.windows.installer: layout, the Settings -> Apps entry, autostart,
install/uninstall into temp folders, the AI part (AiPlan, setup_ai, download, signature
check) and quiet mode."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from adapters.windows.fakes import FakeRegistry
from fakes.secrets import FakeDpapi
from pixelcat import __version__, paths, persistence, settings
from pixelcat.adapters.windows import dpapi as windows_dpapi
from pixelcat.adapters.windows import elevation, hardware, installer, registry, system
from pixelcat.adapters.windows.installer import AiPlan, InstallError, Layout
from pixelcat.adapters.windows.known_folders import shell_folder
from pixelcat.adapters.windows.platform import WindowsSaveMarks, WindowsSecretStore
from pixelcat.llm import external, model_status, models

USER_PROGRAMS = "5CD7AEE2-2219-4A67-B85D-6C9CE15660CB"
START_MENU = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"
DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
# the installer keeps the save with the Windows adapter's secret store and marks
WINDOWS_STORAGE = {"secrets": WindowsSecretStore(), "marks": WindowsSaveMarks()}

STRONG = hardware.Hardware(ram_gb=64, cores=16, cpu="fast", avx2=True, gpus=[], disk_free_gb=500)
MEDIUM = hardware.Hardware(ram_gb=16, cores=8, cpu="ok", avx2=True, gpus=[], disk_free_gb=500)
WEAK = hardware.Hardware(ram_gb=8, cores=4, cpu="old", avx2=False, gpus=[], disk_free_gb=500)


def make_layout(root: Path, base_key: str = r"Software\PixelCatTests\Fake") -> Layout:
    return Layout(root / "Programs" / "PixelCat", root / "StartMenu", root / "Desktop",
                  base_key + r"\Uninstall\PixelCat", base_key + r"\Run", root / "AppData" / "PixelCat",
                  root / "Local" / "PixelCat")


@pytest.fixture
def fake_winreg(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    """An in-memory registry as ``sys.modules["winreg"]`` (the installer does ``import winreg``
    inside its functions, so this also replaces the real one on Windows).  Overrides the
    simpler ``fake_winreg`` of conftest.py: these tests need value types, ``DeleteKey`` and ``fail``."""
    reg = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    return reg


@pytest.fixture
def win_dpapi(monkeypatch: pytest.MonkeyPatch) -> FakeDpapi:
    """A fake DPAPI behind the Windows adapter's own functions (the installer uses that adapter)."""
    dpapi = FakeDpapi()
    monkeypatch.setattr(windows_dpapi, "protect", dpapi.protect)
    monkeypatch.setattr(windows_dpapi, "unprotect", dpapi.unprotect)
    return dpapi


@pytest.fixture
def lay(tmp_path: Path) -> Layout:
    return make_layout(tmp_path / "root")


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "Downloads" / "PixelCat-Setup (1).exe"
    p.parent.mkdir()
    p.write_bytes(b"MZ" + bytes(range(256)) * 12)
    return p


@pytest.fixture
def links(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """elevation.make_lnk writes a small text file instead of a real shortcut."""
    made: list[dict] = []

    def make_lnk(path, target, args="", workdir="", icon="", description=""):  # type: ignore[no-untyped-def]
        made.append({"path": Path(path), "target": Path(target), "workdir": Path(workdir), "icon": Path(icon),
                     "description": description})
        Path(path).write_text(str(target), "utf-8")
        return True

    monkeypatch.setattr(elevation, "make_lnk", make_lnk)
    return made


@pytest.fixture
def root_handlers():  # type: ignore[no-untyped-def]
    """uninstall(delete_data=True) closes the root logger's file handlers: put them back."""
    import logging

    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    for h in before:
        if h not in root.handlers:
            root.addHandler(h)


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    """The PC as the AI part sees it: hardware, Ollama, downloaded models."""
    state = types.SimpleNamespace(hw=STRONG, ollama=tmp_path / "Ollama" / "ollama app.exe")
    monkeypatch.setattr(hardware, "detect", lambda fresh=False: state.hw)
    monkeypatch.setattr(model_status, "find_ollama_app", lambda: state.ollama)

    def downloaded(tag: str) -> None:
        repo, _, name = tag.partition(":")
        p = model_status.models_dir() / "manifests" / "registry.ollama.ai" / "library" / repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")

    state.downloaded = downloaded
    return state


# ---------------------------------------------------------------- layout
def test_layout_for_tests_moves_everything_under_the_test_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PIXELCAT_TEST_ROOT", str(tmp_path))
    lay = installer.layout()
    assert lay == Layout(tmp_path / "Programs" / "PixelCat", tmp_path / "StartMenu", tmp_path / "Desktop",
                         r"Software\PixelCatInstallTest\Uninstall\PixelCat", r"Software\PixelCatInstallTest\Run",
                         tmp_path / "AppData" / "PixelCat", tmp_path / "Local" / "PixelCat")


def test_layout_asks_windows_for_the_known_folders(monkeypatch: pytest.MonkeyPatch, known_folders,  # type: ignore[no-untyped-def]
                                                   tmp_path: Path) -> None:
    monkeypatch.delenv("PIXELCAT_TEST_ROOT", raising=False)
    known_folders.folders.update({USER_PROGRAMS: str(tmp_path / "UP"), START_MENU: str(tmp_path / "SM"),
                                  DESKTOP: str(tmp_path / "Desk")})
    lay = installer.layout()
    assert lay == Layout(tmp_path / "UP" / "PixelCat", tmp_path / "SM", tmp_path / "Desk", installer.UNINSTALL_KEY,
                         system.RUN_KEY, Path(os.environ["APPDATA"]) / "PixelCat",
                         Path(os.environ["LOCALAPPDATA"]) / "PixelCat")
    assert lay.uninstall_key == r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PixelCat"
    assert lay.run_key == r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_layout_falls_back_without_known_folders(monkeypatch: pytest.MonkeyPatch, no_windll) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PIXELCAT_TEST_ROOT", raising=False)
    local, roaming = Path(os.environ["LOCALAPPDATA"]), Path(os.environ["APPDATA"])
    lay = installer.layout()
    assert lay.install_dir == local / "Programs" / "PixelCat"
    assert lay.programs_dir == roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    assert lay.desktop_dir is None and lay.desktop_link is None
    assert lay.data_dir == paths.data_dir()


def test_layout_paths(lay: Layout) -> None:
    assert lay.exe == lay.install_dir / "PixelCat.exe"
    assert lay.start_link == lay.programs_dir / "Pixel Cat.lnk"
    assert lay.desktop_link == lay.desktop_dir / "Pixel Cat.lnk"


@pytest.mark.parametrize("name, setup", [
    ("PixelCat-Setup.exe", True), ("PixelCat-Setup (1).exe", True), ("PixelCatInstaller.exe", True),
    ("pixelcat-SETUP.exe", True), ("PixelCat.exe", False), ("Pixel Cat.exe", False),
])
def test_is_setup_name_for_the_built_exe(monkeypatch: pytest.MonkeyPatch, name: str, setup: bool) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert installer.is_setup_name(Path("C:/Users/me/Downloads") / name) is setup


def test_is_setup_name_never_from_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert installer.is_setup_name("PixelCat-Setup.exe") is False


# ---------------------------------------------------------------- install
def test_install_copies_links_and_registers(lay: Layout, src: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    exe = installer.install(lay, src, desktop_shortcut=True)
    assert exe == lay.exe and exe.read_bytes() == src.read_bytes()
    assert not lay.exe.with_name("PixelCat.exe.new").exists()
    assert [(m["path"], m["target"], m["workdir"], m["icon"]) for m in links] == [
        (lay.start_link, lay.exe, lay.install_dir, lay.exe), (lay.desktop_link, lay.exe, lay.install_dir, lay.exe)]
    assert lay.start_link.is_file() and lay.desktop_link.is_file()
    v = fake_winreg.values(lay.uninstall_key)
    assert {k: v[k] for k in ("DisplayName", "DisplayVersion", "Publisher", "DisplayIcon", "InstallLocation",
                              "UninstallString", "QuietUninstallString", "NoModify", "NoRepair", "EstimatedSize")} == {
        "DisplayName": "Pixel Cat", "DisplayVersion": __version__, "Publisher": "Pixel Cat",
        "DisplayIcon": f"{lay.exe},0", "InstallLocation": str(lay.install_dir),
        "UninstallString": f'"{lay.exe}" --uninstall', "QuietUninstallString": f'"{lay.exe}" --uninstall --quiet',
        "NoModify": 1, "NoRepair": 1, "EstimatedSize": src.stat().st_size // 1024}
    assert re.fullmatch(r"\d{8}", v["InstallDate"])
    t = fake_winreg.types_of(lay.uninstall_key)
    assert t["DisplayName"] == fake_winreg.REG_SZ and t["EstimatedSize"] == fake_winreg.REG_DWORD
    assert installer.installed_version(lay) == __version__


def test_install_estimated_size_is_at_least_1_kb(lay: Layout, tmp_path: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    tiny = tmp_path / "tiny.exe"
    tiny.write_bytes(b"MZ")
    installer.install(lay, tiny, desktop_shortcut=False)
    assert fake_winreg.values(lay.uninstall_key)["EstimatedSize"] == 1


def test_install_without_desktop_shortcut_removes_an_old_one(lay: Layout, src: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    installer.install(lay, src, desktop_shortcut=True)
    installer.install(lay, src, desktop_shortcut=False)
    assert not lay.desktop_link.exists() and lay.start_link.exists()


def test_install_without_a_desktop_folder(tmp_path: Path, src: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    lay = make_layout(tmp_path / "r")
    lay.desktop_dir = None
    installer.install(lay, src, desktop_shortcut=True)
    assert [m["path"] for m in links] == [lay.start_link]


def test_reinstall_over_itself_and_the_download_mark_is_removed(lay: Layout, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ installed")
    zone = str(lay.exe) + ":Zone.Identifier"
    with open(zone, "w") as fh:
        fh.write("[ZoneTransfer]\nZoneId=3\n")
    installer.install(lay, lay.exe, desktop_shortcut=False)
    assert lay.exe.read_bytes() == b"MZ installed"
    assert not os.path.exists(zone)


def test_install_missing_program(lay: Layout, tmp_path: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InstallError, match="can't find the program"):
        installer.install(lay, tmp_path / "nope.exe", desktop_shortcut=True)
    assert not lay.install_dir.exists()


def test_install_fails_without_a_start_menu_entry(lay: Layout, src: Path, monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
                                                  fake_winreg) -> None:
    monkeypatch.setattr(elevation, "make_lnk", lambda *a, **k: False)
    with pytest.raises(InstallError, match="Start menu"):
        installer.install(lay, src, desktop_shortcut=True)
    assert fake_winreg.values(lay.uninstall_key) == {}


def test_install_while_the_old_exe_is_in_use(lay: Layout, src: Path, links, fake_winreg,  # type: ignore[no-untyped-def]
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ old")
    real_replace, tries, sleeps = os.replace, [], []

    def replace(a, b):  # type: ignore[no-untyped-def]
        if Path(b) == lay.exe:
            tries.append(1)
            raise PermissionError(32, "in use")
        return real_replace(a, b)

    monkeypatch.setattr(installer.os, "replace", replace)
    monkeypatch.setattr(installer.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(InstallError, match="still in use"):
        installer.install(lay, src, desktop_shortcut=True)
    assert len(tries) == 20 and sleeps == [0.25] * 20
    assert lay.exe.read_bytes() == b"MZ old"
    assert not lay.exe.with_name("PixelCat.exe.new").exists()


def test_install_waits_for_the_old_exe(lay: Layout, src: Path, links, fake_winreg,  # type: ignore[no-untyped-def]
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    real_replace, busy = os.replace, [2]

    def replace(a, b):  # type: ignore[no-untyped-def]
        if Path(b) == lay.exe and busy[0]:
            busy[0] -= 1
            raise PermissionError(32, "in use")
        return real_replace(a, b)

    monkeypatch.setattr(installer.os, "replace", replace)
    monkeypatch.setattr(installer.time, "sleep", lambda s: None)
    installer.install(lay, src, desktop_shortcut=False)
    assert lay.exe.read_bytes() == src.read_bytes()


def test_installed_version(lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    assert installer.installed_version(lay) is None
    fake_winreg.put(lay.uninstall_key, "DisplayVersion", "0.9.0")
    assert installer.installed_version(lay) is None          # no exe: not installed
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ")
    assert installer.installed_version(lay) == "0.9.0"
    fake_winreg.keys.clear()
    assert installer.installed_version(lay) == "?"


# ---------------------------------------------------------------- autostart
@pytest.mark.parametrize("value, moved", [
    (r'"C:\Old Place\PixelCat.exe"', True), (r'"C:\Python\pythonw.exe" "C:\src\pixelcat\main.py"', True),
    (r'C:\Old\PIXELCAT.EXE', True), (r'"C:\Other\app.exe"', False), ("", False),
])
def test_install_moves_an_existing_autostart(lay: Layout, src: Path, links, fake_winreg,  # type: ignore[no-untyped-def]
                                             value: str, moved: bool) -> None:
    fake_winreg.put(lay.run_key, system.RUN_VALUE, value)
    fake_winreg.put(lay.run_key, "SomethingElse", "other.exe")
    installer.install(lay, src, desktop_shortcut=False)
    assert fake_winreg.values(lay.run_key) == {system.RUN_VALUE: f'"{lay.exe}"' if moved else value,
                                               "SomethingElse": "other.exe"}


def test_install_does_not_add_autostart(lay: Layout, src: Path, links, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    installer.install(lay, src, desktop_shortcut=False)
    assert not fake_winreg.has_key(lay.run_key)


# ---------------------------------------------------------------- uninstall
@pytest.fixture
def installed(lay: Layout, src: Path, links, fake_winreg, win_dpapi) -> Layout:  # type: ignore[no-untyped-def]
    installer.install(lay, src, desktop_shortcut=True)
    persistence.save_state(persistence.SavedState(pets=3), lay.data_dir, **WINDOWS_STORAGE)
    lay.tools_dir.mkdir(parents=True)
    (lay.tools_dir / "ant.exe").write_bytes(b"MZ")
    return lay


def test_uninstall_keeps_the_memory_by_default(installed: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    lay = installed
    assert installer.uninstall(lay, delete_data=False) == []
    assert not lay.start_link.exists() and not lay.desktop_link.exists()
    assert not fake_winreg.has_key(lay.uninstall_key)
    assert installer.installed_version(lay) == "?"           # (the exe is removed by remove_program_folder)
    assert (lay.data_dir / "save.dat").exists() and lay.tools_dir.exists()
    assert fake_winreg.values(registry.REG_PATH) != {}


def test_uninstall_can_delete_the_memory(installed: Layout, fake_winreg, root_handlers) -> None:  # type: ignore[no-untyped-def]
    import logging

    lay = installed
    fh = logging.FileHandler(lay.data_dir / "log.txt")
    root_handlers.addHandler(fh)
    assert installer.uninstall(lay, delete_data=True) == []
    assert not lay.data_dir.exists() and not lay.tools_dir.exists()
    assert fh not in root_handlers.handlers and fh.stream is None
    assert fake_winreg.values(registry.REG_PATH) == {}     # the folder's converted mark is forgotten
    assert lay.install_dir.exists()


@pytest.mark.parametrize("value, removed", [
    ("{exe}", True), ('"{exe}"', True), ('  "{EXE}"  ', True), (r'"C:\Old Place\PixelCat.exe"', False),
])
def test_uninstall_removes_only_its_own_autostart(installed: Layout, fake_winreg, value: str, removed: bool) -> None:  # type: ignore[no-untyped-def]
    lay = installed
    fake_winreg.put(lay.run_key, system.RUN_VALUE, value.format(exe=lay.exe, EXE=str(lay.exe).upper()))
    installer.uninstall(lay, delete_data=False)
    assert (system.RUN_VALUE in fake_winreg.values(lay.run_key)) is not removed


def test_uninstall_twice_is_fine(installed: Layout) -> None:
    installer.uninstall(installed, delete_data=False)
    assert installer.uninstall(installed, delete_data=True) == []


def test_uninstall_reports_what_it_could_not_remove(installed: Layout) -> None:
    lay = installed
    lay.start_link.unlink()
    lay.start_link.mkdir()                           # can't be unlinked
    assert installer.uninstall(lay, delete_data=False) == [str(lay.start_link)]


def test_uninstall_propagates_a_registry_access_error(installed: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    fake_winreg.fail = PermissionError
    with pytest.raises(PermissionError):
        installer.uninstall(installed, delete_data=False)


# ---------------------------------------------------------------- program folder / launch
@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    state = types.SimpleNamespace(calls=[], no_breakaway=False)

    def fake(*a, **k):  # type: ignore[no-untyped-def]
        state.calls.append((a, k))
        if k.get("creationflags", 0) & 0x01000000 and state.no_breakaway:
            raise PermissionError(5, "Access is denied")
        return types.SimpleNamespace(pid=1)

    monkeypatch.setattr(subprocess, "Popen", fake)
    return state


def test_remove_program_folder(lay: Layout, popen) -> None:  # type: ignore[no-untyped-def]
    (lay.install_dir / "sub").mkdir(parents=True)
    lay.exe.write_bytes(b"MZ")
    installer.remove_program_folder(lay)
    assert not lay.install_dir.exists() and popen.calls == []
    installer.remove_program_folder(lay)


def test_remove_program_folder_running_from_it_uses_a_helper(lay: Layout, popen,  # type: ignore[no-untyped-def]
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(lay.exe))
    installer.remove_program_folder(lay)
    assert lay.install_dir.exists()
    ((args, kw),) = popen.calls
    assert args[0].startswith('cmd.exe /d /s /c "') and f'rmdir /s /q "{lay.install_dir}"' in args[0]
    assert kw["cwd"] == str(lay.install_dir.parent)
    assert kw["creationflags"] == 0x08000000 | 0x00000200


def test_launch_starts_the_installed_cat_detached(lay: Layout, popen) -> None:  # type: ignore[no-untyped-def]
    installer.launch(lay.exe)
    ((args, kw),) = popen.calls
    assert args == ([str(lay.exe)],) and kw["cwd"] == str(lay.install_dir)
    assert kw["creationflags"] == 0x00000008 | 0x00000200 | 0x01000000


def test_launch_without_breakaway_when_the_job_forbids_it(lay: Layout, popen) -> None:  # type: ignore[no-untyped-def]
    popen.no_breakaway = True
    installer.launch(lay.exe)
    assert [kw["creationflags"] for _a, kw in popen.calls] == [0x01000208, 0x00000208]


# ---------------------------------------------------------------- download / signature
class FakeResponse:
    def __init__(self, chunks: list[bytes], length: str | None = None, status: int = 200) -> None:
        self.chunks, self.status = chunks, status
        self.headers = {} if length is None else {"Content-Length": length}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def raise_for_status(self) -> None:
        if self.status >= 400:
            import requests

            raise requests.HTTPError(f"{self.status} error")

    def iter_content(self, size: int):  # type: ignore[no-untyped-def]
        assert size == 1 << 20
        yield from self.chunks


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    import requests

    state = types.SimpleNamespace(response=FakeResponse([b"ab", b"cde"], "5"), calls=[])

    def get(url, **kw):  # type: ignore[no-untyped-def]
        state.calls.append((url, kw))
        return state.response

    monkeypatch.setattr(requests, "get", get)
    return state


def test_download_writes_the_file_and_reports_progress(http, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    dest, seen = tmp_path / "setup.exe", []
    installer.download("https://example.invalid/x.exe", dest, lambda d, t: seen.append((d, t)), threading.Event())
    assert dest.read_bytes() == b"abcde" and seen == [(2, 5), (5, 5)]
    assert not (tmp_path / "setup.exe.part").exists()
    assert http.calls == [("https://example.invalid/x.exe", {"stream": True, "timeout": (15, 120)})]


def test_download_without_length(http, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    http.response = FakeResponse([b"abc"])
    seen: list = []
    installer.download("u", tmp_path / "f", lambda d, t: seen.append((d, t)), threading.Event())
    assert seen == [(3, 0)]


def test_download_cancelled(http, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InstallError, match="cancelled"):
        installer.download("u", tmp_path / "f", lambda d, t: None, cancel)
    assert not (tmp_path / "f").exists()


def test_download_http_error(http, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import requests

    http.response = FakeResponse([], status=404)
    with pytest.raises(requests.HTTPError):
        installer.download("u", tmp_path / "f", lambda d, t: None, threading.Event())
    assert not (tmp_path / "f").exists()


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    state = types.SimpleNamespace(stdout="", returncode=0, calls=[])

    def fake(cmd, **kw):  # type: ignore[no-untyped-def]
        state.calls.append((cmd, kw))
        return types.SimpleNamespace(stdout=state.stdout, returncode=state.returncode)

    monkeypatch.setattr(subprocess, "run", fake)
    return state


@pytest.mark.parametrize("stdout, result", [
    ("Valid\r\nCN=Ollama Inc., O=Ollama Inc., C=US\r\n", ("Valid", "CN=Ollama Inc., O=Ollama Inc., C=US")),
    ("NotSigned\r\n\r\n", ("NotSigned", "")),
    ("", ("", "")),
    (None, ("", "")),
])
def test_signature_of(run, tmp_path: Path, stdout, result) -> None:  # type: ignore[no-untyped-def]
    run.stdout = stdout
    assert installer.signature_of(tmp_path / "x.exe") == result
    ((cmd, kw),) = run.calls
    assert cmd[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-AuthenticodeSignature" in cmd[4] and "$env:PIXELCAT_SIGNED_FILE" in cmd[4]
    assert kw["env"]["PIXELCAT_SIGNED_FILE"] == str(tmp_path / "x.exe")       # (never in the command line)
    assert kw["creationflags"] == 0x08000000 and kw["timeout"] == 60


@pytest.mark.parametrize("status, signer, admin, error", [
    ("Valid", "CN=Ollama Inc., O=Ollama Inc., C=US", False, None),
    ("Valid", "CN=Ollama Inc. Evil, O=X", False, "isn't signed by Ollama"),
    ("Valid", "CN=Someone, O=Ollama Inc.", False, "isn't signed by Ollama"),
    ("HashMismatch", "CN=Ollama Inc., O=Ollama Inc.", False, "HashMismatch"),
    ("", "", False, "no signature"),
    ("Valid", "CN=Ollama Inc., O=Ollama Inc., C=US", True, "admin rights"),
])
def test_check_ollama_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, signer: str,
                            admin: bool, error: str | None) -> None:
    monkeypatch.setattr(installer, "signature_of", lambda p: (status, signer))
    monkeypatch.setattr(elevation, "exe_needs_admin", lambda p: admin)
    if error is None:
        installer.check_ollama_setup(tmp_path / "x.exe")
    else:
        with pytest.raises(InstallError, match=error):
            installer.check_ollama_setup(tmp_path / "x.exe")


def test_run_ollama_setup(run, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    installer.run_ollama_setup(tmp_path / "OllamaSetup.exe")
    assert run.calls[0][0] == [str(tmp_path / "OllamaSetup.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    run.returncode = 5
    with pytest.raises(InstallError, match="code 5"):
        installer.run_ollama_setup(tmp_path / "OllamaSetup.exe")


def test_total_ram_falls_back_to_16_gb(no_windll) -> None:  # type: ignore[no-untyped-def]
    assert installer.total_ram_gb() == 16.0


def test_total_ram_from_windows(monkeypatch: pytest.MonkeyPatch, fake_dll) -> None:  # type: ignore[no-untyped-def]
    import ctypes

    k32 = fake_dll("kernel32")

    def status(ref):  # type: ignore[no-untyped-def]
        ref._obj.total = 32 * 2**30
        return 1

    k32.GlobalMemoryStatusEx.result = status
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=k32), raising=False)
    assert installer.total_ram_gb() == 32.0


# ---------------------------------------------------------------- AiPlan
def plan_for(hw, ollama: Path | None = Path("ollama.exe"), ready: bool = False, choice: str = "",  # type: ignore[no-untyped-def]
             advice: bool = True, current: str = "", current_ok: bool = False) -> AiPlan:
    adv = models.advise(hw) if advice else None
    tag = adv.model.tag if adv is not None and adv.model is not None else models.DEFAULT
    return AiPlan(ollama, tag, ready, hw.ram_gb, choice, hw, adv, current, current_ok)


def test_recommended() -> None:
    assert plan_for(STRONG).recommended == "qwen3.5:35b-a3b"
    assert plan_for(MEDIUM).recommended == "qwen3.5:9b"
    assert plan_for(WEAK).recommended == "online"
    assert plan_for(WEAK, advice=False).recommended == models.DEFAULT


def test_options_recommendation_first(machine) -> None:  # type: ignore[no-untyped-def]
    machine.downloaded("qwen3.5:4b")
    opts = plan_for(STRONG).options()
    keys = [k for k, _ in opts]
    assert keys == ["qwen3.5:35b-a3b"] + [m.tag for m in models.MODELS if m.tag != "qwen3.5:35b-a3b"] + ["online", "none"]
    labels = dict(opts)
    assert labels["qwen3.5:35b-a3b"] == "Smart 35B · 22 GB (recommended)"
    assert labels["qwen3.5:122b-a10b"] == "Huge 122B · 81 GB (too big for this PC)"
    assert labels["qwen3.5:27b"] == "Large 27B · 17 GB (slow on this PC)"
    assert labels["qwen3.5:4b"] == "Light 4B · 3.3 GB ✓"
    assert labels["online"] == "Online AI: ChatGPT or Claude"
    assert labels["none"] == "No AI chat for now"


def test_options_when_online_is_recommended() -> None:
    opts = plan_for(WEAK).options()
    assert opts[0] == ("online", "Online AI: ChatGPT or Claude (recommended)")
    assert [k for k, _ in opts].count("online") == 1 and opts[-1][0] == "none"


@pytest.mark.parametrize("current, label", [
    ("qwen3.5:9b", "Medium (9B)"),
    ("anthropic:claude-test-1", None),
    ("online", "an online AI"),
])
def test_options_offer_to_keep_the_current_ai(current: str, label: str | None) -> None:
    opts = plan_for(STRONG, current=current, current_ok=True).options()
    assert opts[0] == ("keep", f"Keep what I use now: {label or external.label(current)}")


@pytest.mark.parametrize("choice, ollama, ready, advice, needed", [
    ("keep", None, False, True, False), ("none", None, False, True, False), ("online", Path("o"), True, False, True),
    ("qwen3.5:9b", Path("o"), True, False, False), ("qwen3.5:9b", Path("o"), True, True, True),
    ("qwen3.5:9b", None, True, False, True), ("qwen3.5:9b", Path("o"), False, False, True),
])
def test_needed(choice: str, ollama, ready: bool, advice: bool, needed: bool) -> None:  # type: ignore[no-untyped-def]
    p = plan_for(MEDIUM, ollama=ollama, ready=ready, choice=choice, advice=advice)
    assert p.needed is needed


def test_local_and_choose(machine) -> None:  # type: ignore[no-untyped-def]
    machine.downloaded("qwen3.5:4b")
    p = plan_for(MEDIUM)
    assert p.local
    p.choose("qwen3.5:4b")
    assert (p.choice, p.model, p.model_ready, p.local) == ("qwen3.5:4b", "qwen3.5:4b", True, True)
    p.choose("online")
    assert (p.choice, p.model, p.local) == ("online", "qwen3.5:4b", False)
    p.choose("2b")                    # a short name models.find knows is kept as typed (not the tag)
    assert (p.choice, p.model, p.model_ready) == ("2b", "2b", False)


def test_describe(machine) -> None:  # type: ignore[no-untyped-def]
    p = plan_for(MEDIUM, ollama=None)
    assert p.describe("keep") == "Nothing changes."
    assert p.describe("none").startswith("No AI for now")
    assert p.describe("online").startswith("ChatGPT or Claude answer from their servers. Nothing to download now.")
    assert p.describe("no-such-model") == ""
    rec = p.describe("qwen3.5:9b")
    assert rec.startswith(p.advice.why)
    assert "Gets Ollama, the free AI engine (1.9 GB" in rec and "The model (6.6 GB) then downloads" in rec
    assert p.describe() == rec
    assert p.describe("qwen3.5:35b-a3b").startswith("Too big for this PC")
    assert p.describe("qwen3.5:0.8b").startswith("Fits, but answers slowly") is False
    assert p.describe("qwen3.5:0.8b").startswith("Tiny 0.8B fits this PC.")


def test_describe_slow_and_ready(machine) -> None:  # type: ignore[no-untyped-def]
    machine.downloaded("qwen3.5:27b")
    p = plan_for(STRONG)
    assert p.describe("qwen3.5:27b") == "Fits, but answers slowly on this CPU."


def test_describe_online_on_a_weak_pc_says_why() -> None:
    p = plan_for(WEAK)
    assert p.describe("online").startswith(p.advice.why + " Nothing to download now.")


def test_describe_without_hardware(machine) -> None:  # type: ignore[no-untyped-def]
    p = AiPlan(Path("o"), "qwen3.5:9b", True, 16.0)
    assert p.describe("qwen3.5:4b") == "Light 4B. The model (3.3 GB) then downloads in the background while the cat runs."
    assert p.describe("qwen3.5:9b") == "Medium 9B."


# ---------------------------------------------------------------- ai_plan
def test_ai_plan_new_install(machine, lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    machine.hw, machine.ollama = MEDIUM, None
    p = installer.ai_plan(lay)
    assert (p.model, p.choice, p.current, p.current_ok, p.ollama, p.model_ready) == \
        ("qwen3.5:9b", "qwen3.5:9b", "", False, None, False)
    assert p.hw is MEDIUM and p.ram_gb == 16


def test_ai_plan_weak_pc_recommends_online(machine, lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    machine.hw = WEAK
    p = installer.ai_plan(lay, updating=False)
    assert p.choice == "online" and p.model == models.DEFAULT


def test_ai_plan_update_keeps_a_model_that_suits_the_pc(machine, lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    settings.save_user_settings({"model": "qwen3.5:9b"}, lay.data_dir)
    machine.downloaded("qwen3.5:9b")
    p = installer.ai_plan(lay, updating=True)
    assert (p.current, p.current_ok, p.choice) == ("qwen3.5:9b", True, "keep")


def test_ai_plan_update_detected_from_the_registry(machine, lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ")
    machine.downloaded(models.DEFAULT)
    p = installer.ai_plan(lay)
    assert (p.current, p.current_ok, p.choice) == (models.DEFAULT, True, "keep")


@pytest.mark.parametrize("user, ollama, on_disk, current, ok", [
    ({"model": "qwen3.5:122b-a10b"}, True, True, "qwen3.5:122b-a10b", False),     # too big
    ({"model": "qwen3.5:9b"}, False, True, "qwen3.5:9b", False),                   # no Ollama
    ({"model": "qwen3.5:9b"}, True, False, "qwen3.5:9b", False),                   # not downloaded
    ({"model": "anthropic:claude-x"}, False, False, "anthropic:claude-x", True),   # an external model
    ({"ai_mode": "online"}, False, False, "online", True),
    ({"ai_mode": "online", "model": "openai:gpt-x"}, False, False, "openai:gpt-x", True),
])
def test_ai_plan_update_current(machine, lay: Layout, fake_winreg, user: dict, ollama: bool, on_disk: bool,  # type: ignore[no-untyped-def]
                                current: str, ok: bool) -> None:
    settings.save_user_settings(user, lay.data_dir)
    if not ollama:
        machine.ollama = None
    if on_disk:
        machine.downloaded(user["model"])
    p = installer.ai_plan(lay, updating=True)
    assert (p.current, p.current_ok) == (current, ok)
    assert p.choice == ("keep" if ok else p.recommended)


def test_ai_plan_with_a_broken_user_config(machine, lay: Layout, fake_winreg, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    def boom(folder=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("broken")

    monkeypatch.setattr(settings, "user_settings", boom)
    assert installer.ai_plan(lay, updating=True).current == models.DEFAULT


# ---------------------------------------------------------------- setup_ai
@pytest.fixture
def ai_env(machine, lay: Layout, fake_winreg, win_dpapi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    """setup_ai with Ollama's download, signature check and installer faked."""
    import tempfile

    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    state = types.SimpleNamespace(progress=[], ran=[], signature=("Valid", "CN=Ollama Inc., O=Ollama Inc."),
                                  found=Path("ollama.exe"), downloads=[], temp=temp)

    def download(url, dest, progress, cancel):  # type: ignore[no-untyped-def]
        state.downloads.append(url)
        dest.with_name(dest.name + ".part").write_bytes(b"MZ")
        dest.write_bytes(b"MZ")
        progress(500_000_000, 1_000_000_000)
        progress(700_000_000, 0)

    monkeypatch.setattr(installer, "download", download)
    monkeypatch.setattr(installer, "signature_of", lambda p: state.signature)
    monkeypatch.setattr(elevation, "exe_needs_admin", lambda p: False)
    state.run = lambda **kw: installer.setup_ai(lay, kw.pop("plan"), state.progress.append, threading.Event(),
                                                runner=state.ran.append, finder=lambda: state.found, **kw)
    return state


def test_setup_ai_keep_or_none_does_nothing(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    for choice in ("keep", "none"):
        assert ai_env.run(plan=plan_for(MEDIUM, ollama=None, choice=choice)) == ""
    assert ai_env.downloads == [] and not (lay.data_dir / "config.json").exists()


def test_setup_ai_online(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    note = ai_env.run(plan=plan_for(WEAK, ollama=None, choice="online"))
    assert "online AI" in note and ai_env.downloads == []
    assert settings.user_settings(lay.data_dir) == {"ai_mode": "online", "preload_on_start": False}
    assert not (lay.data_dir / "save.dat").exists()


def test_setup_ai_ready_model_on_a_strong_pc(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    p = plan_for(STRONG, ready=True)
    p.choose(p.recommended)
    p.model_ready = True
    assert ai_env.run(plan=p) == "The AI chat is ready."
    assert not (lay.data_dir / "config.json").exists()           # the default model: nothing to write
    st = persistence.load_state(lay.data_dir, **WINDOWS_STORAGE)
    assert st.model_download_ok is True


def test_setup_ai_light_pc_loads_the_ai_only_when_needed(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    settings.save_user_settings({"ai_mode": "online", "think": False}, lay.data_dir)
    p = plan_for(MEDIUM)
    p.choose("qwen3.5:9b")
    note = ai_env.run(plan=p)
    assert note.startswith("The AI model qwen3.5:9b (6.6 GB) downloads in the background")
    assert settings.user_settings(lay.data_dir) == {"ai_mode": "local", "think": False, "model": "qwen3.5:9b",
                                                    "preload_on_start": False, "keep_alive": "3m"}


def test_setup_ai_too_big_model_never_preloads(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    p = plan_for(STRONG)
    p.choose("qwen3.5:122b-a10b")
    ai_env.run(plan=p)
    assert settings.user_settings(lay.data_dir) == {"model": "qwen3.5:122b-a10b", "preload_on_start": False,
                                                    "keep_alive": "1m"}


def test_setup_ai_default_model_written_when_the_user_had_another(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    settings.save_user_settings({"model": "qwen3.5:4b"}, lay.data_dir)
    p = plan_for(STRONG, ready=True)
    p.choose(models.DEFAULT)
    ai_env.run(plan=p)
    assert settings.user_settings(lay.data_dir)["model"] == models.DEFAULT


def test_setup_ai_installs_ollama_first(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    p = plan_for(MEDIUM, ollama=None)
    p.choose("qwen3.5:9b")
    ai_env.run(plan=p)
    setup = ai_env.temp / "PixelCat-OllamaSetup.exe"
    assert ai_env.downloads == [installer.OLLAMA_SETUP_URL]
    assert ai_env.ran == [setup]
    assert ai_env.progress == ["Downloading Ollama… 50% (0.5 of 1.0 GB)", "Downloading Ollama… (0.7 GB)",
                               "Checking that it really comes from Ollama…", "Installing Ollama… (about a minute)"]
    assert list(ai_env.temp.iterdir()) == []                     # the setup and .part are deleted
    assert persistence.load_state(lay.data_dir, **WINDOWS_STORAGE).model_download_ok


def test_setup_ai_refuses_an_unsigned_ollama_setup(ai_env, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    ai_env.signature = ("NotSigned", "")
    p = plan_for(MEDIUM, ollama=None)
    with pytest.raises(InstallError, match="isn't signed by Ollama"):
        ai_env.run(plan=p)
    assert ai_env.ran == [] and list(ai_env.temp.iterdir()) == []
    assert not (lay.data_dir / "config.json").exists()


def test_setup_ai_ollama_not_found_after_installing(ai_env) -> None:  # type: ignore[no-untyped-def]
    ai_env.found = None
    with pytest.raises(InstallError, match="can't find Ollama"):
        ai_env.run(plan=plan_for(MEDIUM, ollama=None))


# ---------------------------------------------------------------- quiet mode
@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch, lay: Layout):  # type: ignore[no-untyped-def]
    state = types.SimpleNamespace(closing="not running", calls=[], left=[], install_error=None,
                                  plan=None, ai_error=None)
    monkeypatch.setattr(installer, "layout", lambda: lay)
    monkeypatch.setattr(system, "close_running", lambda t: (state.calls.append(("close", t)), state.closing)[1])

    def install(l, s, desktop):  # type: ignore[no-untyped-def]
        state.calls.append(("install", s, desktop))
        if state.install_error:
            raise state.install_error
        return l.exe

    def setup_ai(l, plan, progress, cancel):  # type: ignore[no-untyped-def]
        state.calls.append(("setup_ai", plan.choice))
        if state.ai_error:
            raise state.ai_error
        return "ok"

    monkeypatch.setattr(installer, "install", install)
    monkeypatch.setattr(installer, "uninstall", lambda l, d: (state.calls.append(("uninstall", d)), state.left)[1])
    monkeypatch.setattr(installer, "remove_program_folder", lambda l: state.calls.append(("remove", l)))
    monkeypatch.setattr(installer, "launch", lambda exe: state.calls.append(("launch", exe)))
    monkeypatch.setattr(installer, "ai_plan", lambda l: state.plan)
    monkeypatch.setattr(installer, "setup_ai", setup_ai)
    return state


def args(**kw) -> types.SimpleNamespace:  # type: ignore[no-untyped-def]
    return types.SimpleNamespace(**{"uninstall": False, **kw})


def test_quiet_uninstall(quiet, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    assert installer.run_quiet(args(uninstall=True, delete_data=True)) == 0
    assert quiet.calls == [("close", 10.0), ("uninstall", True), ("remove", lay)]


def test_quiet_uninstall_with_leftovers(quiet) -> None:  # type: ignore[no-untyped-def]
    quiet.left = ["x"]
    assert installer.run_quiet(args(uninstall=True)) == 1
    assert ("uninstall", False) in quiet.calls


@pytest.mark.parametrize("uninstall", [True, False])
def test_quiet_while_the_cat_keeps_running(quiet, uninstall: bool) -> None:  # type: ignore[no-untyped-def]
    quiet.closing = "still running"
    assert installer.run_quiet(args(uninstall=uninstall)) == 2
    assert quiet.calls == [("close", 10.0)]


def test_quiet_install(quiet, lay: Layout, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "executable", str(lay.exe.parent.parent / "PixelCat-Setup.exe"))
    assert installer.run_quiet(args()) == 0
    assert quiet.calls == [("close", 10.0), ("install", Path(sys.executable), True)]


def test_quiet_install_options(quiet, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    assert installer.run_quiet(args(no_desktop_shortcut=True, launch=True)) == 0
    assert quiet.calls[1][2] is False and quiet.calls[-1] == ("launch", lay.exe)


@pytest.mark.parametrize("error", [InstallError("nope"), PermissionError(5, "denied")])
def test_quiet_install_fails(quiet, error: Exception) -> None:  # type: ignore[no-untyped-def]
    quiet.install_error = error
    assert installer.run_quiet(args(launch=True)) == 1
    assert not any(c[0] == "launch" for c in quiet.calls)


def test_quiet_install_with_ai(quiet) -> None:  # type: ignore[no-untyped-def]
    quiet.plan = plan_for(MEDIUM, ollama=None, choice="qwen3.5:9b")
    assert installer.run_quiet(args(with_ai=True)) == 0
    assert quiet.calls[-1] == ("setup_ai", "qwen3.5:9b")


def test_quiet_install_with_ai_keeps_a_suitable_current_ai(quiet) -> None:  # type: ignore[no-untyped-def]
    quiet.plan = plan_for(MEDIUM, choice="keep", current="qwen3.5:9b", current_ok=True)
    assert installer.run_quiet(args(with_ai=True)) == 0
    assert not any(c[0] == "setup_ai" for c in quiet.calls)


def test_quiet_install_with_ai_failure_still_installs_and_launches(quiet, lay: Layout) -> None:  # type: ignore[no-untyped-def]
    quiet.plan = plan_for(MEDIUM, ollama=None, choice="qwen3.5:9b")
    quiet.ai_error = RuntimeError("download failed")
    assert installer.run_quiet(args(with_ai=True, launch=True)) == 3
    assert quiet.calls[-1] == ("launch", lay.exe)


# ---------------------------------------------------------------- the window (smoke)
@pytest.fixture
def window_env(qapp, monkeypatch: pytest.MonkeyPatch, lay: Layout, fake_winreg, src: Path):  # type: ignore[no-untyped-def]
    from pixelcat.sprites.library import SpriteLibrary

    state = types.SimpleNamespace(calls=[], closing="not running", plan=plan_for(MEDIUM, choice="qwen3.5:9b"))
    monkeypatch.setattr(installer, "ai_plan", lambda l: state.plan)
    monkeypatch.setattr(system, "close_running", lambda t: state.closing)
    monkeypatch.setattr(installer, "install", lambda l, s, d: state.calls.append(("install", d)) or l.exe)
    monkeypatch.setattr(installer, "uninstall", lambda l, d: state.calls.append(("uninstall", d)) or ["leftover"])
    monkeypatch.setattr(installer, "setup_ai", lambda l, p, prog, cancel: state.calls.append(("setup_ai", p.choice))
                        or "AI note")
    monkeypatch.setattr(installer, "launch", lambda exe: state.calls.append(("launch", exe)))
    state.make = lambda mode: installer.InstallerWindow(mode, lay, src, SpriteLibrary("charcoal"))
    return state


@pytest.mark.qt
def test_install_window_flow(window_env, qtbot) -> None:  # type: ignore[no-untyped-def]
    w = window_env.make("install")
    qtbot.addWidget(w)
    assert w.windowTitle() == "Pixel Cat Setup" and w.step == "confirm"
    assert w.ok.text() == "INSTALL" and w.box.isChecked()
    keys = [w.ai_combo.itemData(i) for i in range(w.ai_combo.count())]
    assert keys == [k for k, _ in window_env.plan.options()]
    assert w.ai_combo.currentData() == "qwen3.5:9b"
    w.ai_combo.setCurrentIndex(keys.index("none"))
    assert w.ai_note.text().startswith("No AI for now")
    w.ai_combo.setCurrentIndex(keys.index("qwen3.5:9b"))
    w.box.setChecked(False)
    w._on_ok()
    qtbot.waitUntil(lambda: w.step == "done", timeout=5000)
    assert window_env.calls == [("install", False), ("setup_ai", "qwen3.5:9b")]
    assert w.exit_code == 0 and w.ok.text() == "START PIXEL CAT" and w.status.text() == "AI note"
    w._on_ok()
    assert window_env.calls[-1] == ("launch", w.lay.exe)


@pytest.mark.qt
def test_install_window_cat_still_running(window_env, qtbot) -> None:  # type: ignore[no-untyped-def]
    window_env.closing = "still running"
    w = window_env.make("install")
    qtbot.addWidget(w)
    w._on_ok()
    qtbot.waitUntil(lambda: w.step == "error", timeout=5000)
    assert w.ok.text() == "TRY AGAIN" and "still running" in w.text.text()
    assert window_env.calls == [] and w.exit_code == 1


@pytest.mark.qt
def test_install_window_shows_an_install_error(window_env, qtbot, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    def fail(l, s, d):  # type: ignore[no-untyped-def]
        raise InstallError("couldn't create the Start menu entry")

    monkeypatch.setattr(installer, "install", fail)
    w = window_env.make("install")
    qtbot.addWidget(w)
    w._on_ok()
    qtbot.waitUntil(lambda: w.step == "error", timeout=5000)
    assert "Start menu entry" in w.text.text() and w.exit_code == 1


@pytest.mark.qt
def test_install_window_update_screen(window_env, qtbot, lay: Layout, fake_winreg) -> None:  # type: ignore[no-untyped-def]
    lay.install_dir.mkdir(parents=True)
    lay.exe.write_bytes(b"MZ")
    fake_winreg.put(lay.uninstall_key, "DisplayVersion", "0.9.0")
    w = window_env.make("install")
    qtbot.addWidget(w)
    assert w.ok.text() == "UPDATE" and f"0.9.0 → {__version__}" in w.text.text()
    assert not w.box.isChecked()                          # no desktop shortcut before: stays off


@pytest.mark.qt
def test_uninstall_window_flow(window_env, qtbot) -> None:  # type: ignore[no-untyped-def]
    w = window_env.make("uninstall")
    qtbot.addWidget(w)
    assert w.windowTitle() == "Uninstall Pixel Cat" and w.ok.text() == "UNINSTALL"
    assert not w.box.isChecked() and w.ai_combo.isHidden()
    w.box.setChecked(True)
    w._on_ok()
    qtbot.waitUntil(lambda: w.step == "done", timeout=5000)
    assert window_env.calls == [("uninstall", True)]
    assert w.remove_folder_on_exit and "leftover" in w.text.text() and w.ok.isHidden()


# ---------------------------------------------------------------- real Windows (test registry key only)
@pytest.fixture
def real_src(tmp_path: Path) -> Path:
    """A real program (a copy of this Python), like the real installer always installs."""
    p = tmp_path / "Downloads" / "PixelCat-Setup (1).exe"
    p.parent.mkdir()
    p.write_bytes(Path(sys.executable).read_bytes())
    return p


@pytest.fixture
def real_lay(tmp_path: Path, test_reg_key: str, real_profile, monkeypatch: pytest.MonkeyPatch) -> Layout:  # type: ignore[no-untyped-def]
    """Everything the install writes goes to tmp_path and the test registry key; only the
    environment is the real profile's, because the shell link code resolves Known Folders."""
    monkeypatch.setattr(registry, "REG_PATH", test_reg_key + r"\ProtectedSaves")
    return make_layout(tmp_path / "root", test_reg_key)


@pytest.mark.windows
def test_real_install_and_uninstall(real_lay: Layout, real_src: Path, real_reg_read, root_handlers) -> None:  # type: ignore[no-untyped-def]
    import winreg

    lay, src = real_lay, real_src
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, lay.run_key) as k:
        winreg.SetValueEx(k, system.RUN_VALUE, 0, winreg.REG_SZ, r'"C:\Old Place\PixelCat.exe"')
    installer.install(lay, src, desktop_shortcut=True)
    assert lay.exe.read_bytes() == src.read_bytes()
    assert os.path.samefile(elevation.lnk_target(lay.start_link)[0], lay.exe)
    assert lay.desktop_link.exists()
    assert real_reg_read(lay.uninstall_key, "DisplayName") == "Pixel Cat"
    assert real_reg_read(lay.uninstall_key, "UninstallString") == f'"{lay.exe}" --uninstall'
    assert real_reg_read(lay.uninstall_key, "NoModify") == 1
    assert real_reg_read(lay.run_key, system.RUN_VALUE) == f'"{lay.exe}"'
    assert installer.installed_version(lay) == __version__
    assert installer.uninstall(lay, delete_data=True) == []
    assert not lay.start_link.exists() and not lay.desktop_link.exists()
    assert real_reg_read(lay.uninstall_key, "DisplayName") is None
    assert real_reg_read(lay.run_key, system.RUN_VALUE) is None
    installer.remove_program_folder(lay)
    assert not lay.install_dir.exists()


@pytest.mark.windows
def test_real_start_menu_link_with_the_installers_arguments(real_profile, real_src: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    exe = tmp_path / "Programs" / "PixelCat" / installer.EXE_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(real_src.read_bytes())
    link = tmp_path / "StartMenu" / installer.LINK_NAME
    link.parent.mkdir()
    assert elevation.make_lnk(link, exe, workdir=exe.parent, icon=exe,
                              description="Pixel Cat - a pixel-art desktop pet with a local AI chat")
    assert os.path.samefile(elevation.lnk_target(link)[0], exe)


@pytest.mark.windows
def test_real_known_folders_layout(real_profile, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PIXELCAT_TEST_ROOT", raising=False)
    start_menu = installer._known(START_MENU)
    assert start_menu is not None and start_menu.is_dir(), f"Start menu folder: {start_menu}"
    lay = installer.layout()
    assert lay.install_dir.name == "PixelCat" and lay.install_dir.parent.name.lower() == "programs"
    assert lay.programs_dir == start_menu and lay.desktop_dir == shell_folder("desktop")
    assert lay.uninstall_key == installer.UNINSTALL_KEY       # read only: nothing is written here


@pytest.mark.windows
def test_real_total_ram() -> None:
    assert 1 < installer.total_ram_gb() < 4096
