"""pixelcat.desktop.elevation: "does this need admin rights?" checks and .lnk shortcuts (mocked)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from desktop.fakes import FakeCom, FakeDLL, FakeWinreg, raising, write_ptr
from pixelcat.desktop import control, elevation

P = ctypes.c_void_p
LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"


@pytest.fixture
def elev(monkeypatch):  # type: ignore[no-untyped-def]
    ns = SimpleNamespace(k32=FakeDLL("kernel32"), ole32=FakeDLL("ole32"), advapi32=FakeDLL("advapi32"),
                         reg=FakeWinreg())
    for name in ("k32", "ole32", "advapi32"):
        monkeypatch.setattr(elevation, name, getattr(ns, name))
    monkeypatch.setattr(elevation, "winreg", ns.reg)
    return ns


# ---------------------------------------------------------------- elevation_type
@pytest.mark.parametrize("opened, got_info, value, expected", [(1, 1, 3, 3), (1, 1, 2, 2), (1, 0, 3, 1), (0, 1, 3, 1)])
def test_elevation_type(elev, opened, got_info, value, expected):
    def open_token(proc, access, tok):  # type: ignore[no-untyped-def]
        tok._obj.value = 0x70
        return opened

    def info(tok, cls, buf, size, ret):  # type: ignore[no-untyped-def]
        assert cls == elevation.TokenElevationType
        buf._obj.value = value
        return got_info

    elev.advapi32.OpenProcessToken.result = open_token
    elev.advapi32.GetTokenInformation.result = info
    assert elevation.elevation_type() == expected
    assert [c[0].value for c in elev.k32.CloseHandle.calls] == ([0x70] if opened else [])


# ---------------------------------------------------------------- manifests
def manifests(elev, resources: dict[int, bytes] | None, keep: list):  # type: ignore[no-untyped-def]
    """resources: resource id -> manifest bytes (None: the file can't be loaded)."""
    elev.k32.LoadLibraryExW.result = lambda exe, f, flags: 0x400000 if resources is not None else 0
    elev.k32.FindResourceW.result = lambda h, rid, kind: (rid.value if rid.value in (resources or {}) else 0) \
        if kind.value == elevation.RT_MANIFEST else 0
    elev.k32.SizeofResource.result = lambda h, res: len(resources[res])
    elev.k32.LoadResource.result = lambda h, res: res

    def lock(res):  # type: ignore[no-untyped-def]
        buf = ctypes.create_string_buffer(resources[res])
        keep.append(buf)
        return ctypes.addressof(buf) if resources[res] else 0

    elev.k32.LockResource.result = lock


def manifest(level: str | None) -> bytes:
    trust = f'<requestedExecutionLevel level="{level}" uiAccess="false"/>' if level else ""
    return f'<?xml version="1.0"?><assembly><trustInfo><security><requestedPrivileges>{trust}' \
           f'</requestedPrivileges></security></trustInfo></assembly>'.encode()


@pytest.mark.parametrize("resources, expected", [
    ({1: manifest("requireAdministrator")}, "requireAdministrator"),
    ({2: manifest("highestAvailable")}, "highestAvailable"),
    ({1: manifest("asInvoker")}, "asInvoker"),
    ({1: b"<requestedExecutionLevel   level = 'asInvoker' />"}, "asInvoker"),
    ({3: manifest(None)}, ""),
    ({1: b""}, None),
    ({}, None),
    (None, None),
])
def test_manifest_level(elev, resources, expected):
    keep: list = []
    manifests(elev, resources, keep)
    assert elevation.manifest_level(r"C:\Apps\x.exe") == expected
    assert elev.k32.FreeLibrary.calls == ([(0x400000,)] if resources is not None else [])


@pytest.fixture
def exe(tmp_path):  # type: ignore[no-untyped-def]
    def make(name: str) -> str:
        p = tmp_path / name
        p.write_bytes(b"MZ")
        return str(p)
    return make


@pytest.mark.parametrize("name, level, elevation_type, expected", [
    ("tool.exe", "requireAdministrator", 1, True),
    ("tool.exe", "highestAvailable", 3, True),        # an admin with UAC gets asked
    ("tool.exe", "highestAvailable", 1, False),
    ("tool.exe", "asInvoker", 1, False),
    ("tool.exe", None, 1, False),
    ("setup.exe", None, 1, True),                     # installer detection, no manifest
    ("MyApp_Installer.exe", None, 1, True),
    ("uninst64.exe", None, 1, True),
    ("setup.exe", "asInvoker", 1, False),             # a manifest switches the detection off
    ("setup.exe", "", 1, False),
])
def test_exe_needs_admin(elev, monkeypatch, exe, name, level, elevation_type, expected):
    monkeypatch.setattr(elevation, "manifest_level", lambda path: level)
    monkeypatch.setattr(elevation, "elevation_type", lambda: elevation_type)
    assert elevation.exe_needs_admin(exe(name)) is expected


def test_exe_needs_admin_missing_file(elev, tmp_path):
    assert elevation.exe_needs_admin("") is False
    assert elevation.exe_needs_admin(str(tmp_path / "setup.exe")) is False


@pytest.mark.parametrize("root", ["HKCU", "HKLM"])
def test_compatibility_run_as_admin_flag(elev, monkeypatch, exe, root):
    monkeypatch.setattr(elevation, "manifest_level", raising(AssertionError("not needed")))
    path = exe("game.exe")
    elev.reg.add(f"{root}\\{LAYERS}", **{path: "~ HIGHDPIAWARE RunAsAdmin"})
    assert elevation.exe_needs_admin(path) is True


def test_compatibility_flags_without_run_as_admin(elev, monkeypatch, exe):
    monkeypatch.setattr(elevation, "manifest_level", lambda path: "asInvoker")
    path = exe("game.exe")
    elev.reg.add(f"HKCU\\{LAYERS}", **{path: "~ HIGHDPIAWARE"})
    assert elevation._compat_runas(path) is False
    assert elevation.exe_needs_admin(path) is False


# ---------------------------------------------------------------- needs_admin (open_app targets)
@pytest.fixture
def checks(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    seen: list = []
    monkeypatch.setattr(elevation, "exe_needs_admin", lambda e: seen.append(e) or e.endswith("admin.exe"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    return seen


def test_needs_admin_targets(checks, monkeypatch, tmp_path):
    absolute = str(tmp_path / "admin.exe")
    monkeypatch.setattr(elevation.shutil, "which", lambda name: {"tool": str(tmp_path / "tool.exe")}.get(name))
    links = {"run.lnk": (str(tmp_path / "x.exe"), True), "plain.lnk": (str(tmp_path / "admin.exe"), False),
             "mmc.lnk": (str(tmp_path / "compmgmt.msc"), False), "user.lnk": (str(tmp_path / "x.exe"), False)}
    monkeypatch.setattr(elevation, "lnk_target", lambda p: links[os.path.basename(p)])
    cases = [
        ([absolute], True), (["tool", "--flag"], False), (["missing"], False),
        (["https://example.com"], False), (["ms-settings:display"], False), ([], False),
        ("shell:AppsFolder\\Microsoft.Photos!App", False),
        (str(tmp_path / "run.lnk"), True), (str(tmp_path / "plain.lnk"), True), (str(tmp_path / "user.lnk"), False),
        (str(tmp_path / "mmc.lnk"), False),
        (str(tmp_path / "admin.exe"), True), (str(tmp_path / "Report.pdf"), False), (None, False),
    ]
    assert [elevation.needs_admin(t) for t, _ in cases] == [e for _, e in cases]
    assert os.path.join(str(tmp_path / "Windows"), "System32", "mmc.exe") in checks
    assert str(tmp_path / "tool.exe") in checks and "" in checks


def test_needs_admin_never_raises(monkeypatch):
    monkeypatch.setattr(elevation, "lnk_target", raising(RuntimeError("COM broke")))
    assert elevation.needs_admin("x.lnk") is False


@pytest.mark.parametrize("procs, expected", [
    ([control.Proc(1, 0, "System"), control.Proc(2, 1, "Consent.exe")], True),
    ([control.Proc(1, 0, "explorer.exe")], False),
])
def test_uac_prompt_open(monkeypatch, procs, expected):
    monkeypatch.setattr(control, "list_processes", lambda: procs)
    assert elevation.uac_prompt_open() is expected


def test_uac_prompt_open_errors(monkeypatch):
    monkeypatch.setattr(control, "list_processes", raising(OSError("x")))
    assert elevation.uac_prompt_open() is False


# ---------------------------------------------------------------- .lnk via IShellLinkW (fake COM)
@pytest.fixture
def shell_link(elev):  # type: ignore[no-untyped-def]
    st = SimpleNamespace(path=r"C:\Program Files\App\app.exe", create_hr=0, qi_hr=0, load_hr=0, save_hr=0,
                         init_hr=0, set={}, loaded=None, saved=None)

    def load(path, mode):  # type: ignore[no-untyped-def]
        st.loaded = path
        return st.load_hr

    def save(path, remember):  # type: ignore[no-untyped-def]
        st.saved = (path, remember)
        return st.save_hr

    st.pf = FakeCom({5: ((ctypes.c_wchar_p, wintypes.DWORD), load), 6: ((ctypes.c_wchar_p, wintypes.BOOL), save)})

    def qi(riid, pp):  # type: ignore[no-untyped-def]
        if st.qi_hr:
            return st.qi_hr
        write_ptr(pp, st.pf.ptr)

    def get_path(buf, size, fd, flags):  # type: ignore[no-untyped-def]
        text = ctypes.create_unicode_buffer(st.path)
        ctypes.memmove(buf, text, ctypes.sizeof(text))

    def setter(name):  # type: ignore[no-untyped-def]
        return lambda *a: st.set.__setitem__(name, a)

    st.link = FakeCom({0: ((P, P), qi), 3: ((P, ctypes.c_int, P, wintypes.DWORD), get_path),
                       20: ((ctypes.c_wchar_p,), setter("path")), 11: ((ctypes.c_wchar_p,), setter("args")),
                       9: ((ctypes.c_wchar_p,), setter("workdir")), 7: ((ctypes.c_wchar_p,), setter("description")),
                       17: ((ctypes.c_wchar_p, ctypes.c_int), setter("icon"))})

    def create(clsid, outer, ctx, iid, out):  # type: ignore[no-untyped-def]
        if st.create_hr:
            return st.create_hr
        out._obj.value = st.link.ptr
        return 0

    elev.ole32.CoCreateInstance.result = create
    elev.ole32.CoInitializeEx.result = lambda *a: st.init_hr
    st.ole32 = elev.ole32
    return st


def lnk_file(tmp_path, runas: bool, name: str = "app.lnk"):  # type: ignore[no-untyped-def]
    p = tmp_path / name
    flags = 0x2000 | 0x1 if runas else 0x1
    p.write_bytes(b"\x4c\x00\x00\x00" + b"\x00" * 16 + flags.to_bytes(4, "little") + b"\x00" * 40)
    return p


@pytest.mark.parametrize("runas", [True, False])
def test_lnk_target_reads_path_and_run_as_flag(shell_link, tmp_path, runas):
    p = lnk_file(tmp_path, runas)
    assert elevation.lnk_target(p) == (shell_link.path, runas)
    assert shell_link.loaded == str(p)
    assert (shell_link.link.released, shell_link.pf.released) == (1, 1)
    assert len(shell_link.ole32.CoUninitialize.calls) == 1


@pytest.mark.parametrize("field, hr, pf_released", [("create_hr", -1, 0), ("qi_hr", -2147467262, 0),
                                                    ("load_hr", -2147024894, 1)])
def test_lnk_target_failures_keep_the_flag(shell_link, tmp_path, field, hr, pf_released):
    setattr(shell_link, field, hr)
    assert elevation.lnk_target(lnk_file(tmp_path, True)) == ("", True)
    assert shell_link.pf.released == pf_released
    assert shell_link.link.released == (0 if field == "create_hr" else 1)


def test_lnk_target_unreadable_file_and_sta_thread(shell_link, tmp_path):
    shell_link.init_hr = -2147417850
    assert elevation.lnk_target(tmp_path / "missing.lnk") == (shell_link.path, False)
    assert shell_link.ole32.CoUninitialize.calls == []


def test_make_lnk_sets_everything_and_saves(shell_link, tmp_path):
    target = tmp_path / "app.exe"
    assert elevation.make_lnk(tmp_path / "App.lnk", target, args="--tray", workdir=tmp_path, icon=target,
                              description="d" * 300) is True
    assert shell_link.set == {"path": (str(target),), "args": ("--tray",), "workdir": (str(tmp_path),),
                              "description": ("d" * 250,), "icon": (str(target), 0)}
    assert shell_link.saved == (str(tmp_path / "App.lnk"), 1)
    assert (shell_link.link.released, shell_link.pf.released) == (1, 1)


def test_make_lnk_minimal_and_failures(shell_link, tmp_path):
    assert elevation.make_lnk(tmp_path / "a.lnk", "C:/x.exe") is True
    assert set(shell_link.set) == {"path"}
    shell_link.save_hr = -2147024891
    assert elevation.make_lnk(tmp_path / "a.lnk", "C:/x.exe") is False
    shell_link.qi_hr = -1
    assert elevation.make_lnk(tmp_path / "a.lnk", "C:/x.exe") is False
    shell_link.create_hr = -1
    assert elevation.make_lnk(tmp_path / "a.lnk", "C:/x.exe") is False
