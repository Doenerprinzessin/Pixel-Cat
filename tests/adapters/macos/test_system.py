"""pixelcat.adapters.macos.system: the instance lock, osascript dialogs and volume, system information,
locking the screen and media keys - all with fakes (live: read-only)."""
from __future__ import annotations

import re
import subprocess

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, hardware, system
from pixelcat.domain import PermissionNeeded

BATT = ("Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=27983971)\t83%; discharging; 4:12 remaining present: true\n")
VM_STAT = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
           "Pages free:                                    10000.\n"
           "Pages active:                                 400000.\n"
           "Pages inactive:                               200000.\n"
           "Pages speculative:                             52144.\n"
           "\"Translation faults\":                      442986656.\n")


@pytest.fixture
def sysctl(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer(["/usr/sbin/sysctl", "-n", "hw.memsize"], "17179869184\n")
    fake_run.answer(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                    "{ sec = 1000, usec = 5 } Fri Nov 27 09:01:16 2026\n")
    return fake_run


@pytest.fixture(autouse=True)
def no_lock_left(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(system, "_lock_file", None)
    yield
    if system._lock_file is not None:
        system._lock_file.close()


# ------------------------------------------------------------------ the instance
def test_the_instance_comes_from_the_environment(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PIXELCAT_INSTANCE", "-test7")
    assert system.MacSystem().instance == "-test7"


def test_a_second_cat_of_the_same_instance_is_refused(monkeypatch, isolated_dirs):  # type: ignore[no-untyped-def]
    import fcntl

    from pixelcat import paths

    monkeypatch.setenv("PIXELCAT_INSTANCE", "-lock")
    assert system.MacSystem().acquire_single_instance() is True
    lock = paths.data_dir() / "instance-lock.lock"
    assert lock.exists() and system._lock_file is not None
    assert system.MacSystem().acquire_single_instance() is True       # the owner asking again
    with open(lock, "a+") as other:                                   # another process' view: it's taken
        with pytest.raises(BlockingIOError):
            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = system._lock_file
    monkeypatch.setattr(system, "_lock_file", None)                   # "another cat" in this process
    assert system.MacSystem().acquire_single_instance() is False
    held.close()
    assert system.MacSystem().acquire_single_instance() is True       # the first cat is gone


def test_no_installer_asks_the_cat_to_quit():
    s = system.MacSystem()
    assert s.create_quit_event() == 0 and s.quit_requested(0) is False


# ------------------------------------------------------------------ dialogs and the volume
def test_the_message_box_passes_the_text_as_data_not_as_script(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT], "button returned:OK")
    text = 'Already "running" \\ -e do shell script "rm -rf ~" 🐾'
    system.MacSystem().message_box(text, "Pixel Cat")
    argv = fake_run.calls[0]
    assert argv[-3:] == ["--", text, "Pixel Cat"]
    script = " ".join(argv[1:-3])
    assert "rm -rf" not in script and "display dialog (item 1 of argv)" in script


def test_a_message_box_that_cant_show_only_logs(fake_run, caplog):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT], returncode=1, stderr="execution error: no user interaction")
    system.MacSystem().message_box("hello")
    assert "hello" in caplog.text


def test_get_volume_parses_the_settings(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT, "-e", "get volume settings"],
                    "output volume:37, input volume:50, alert volume:100, output muted:true\n")
    assert system.MacSystem().get_volume() == (37, True)


def test_an_output_without_volume_says_so(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT, "-e", "get volume settings"],
                    "output volume:missing value, input volume:50, alert volume:100, output muted:missing value")
    with pytest.raises(OSError, match="no volume control"):
        system.MacSystem().get_volume()


def test_set_volume_clamps_and_unmutes(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT])
    s = system.MacSystem()
    assert s.set_volume(140) == 100 and s.set_volume(-3) == 0
    assert [c[2] for c in fake_run.calls] == ["set volume output volume 100 without output muted",
                                              "set volume output volume 0"]
    s.set_mute(True)
    s.set_mute(False)
    assert [c[2] for c in fake_run.calls[2:]] == ["set volume output muted true", "set volume output muted false"]


def test_a_failing_osascript_raises_oserror(fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer([system.OSASCRIPT], returncode=1, stderr="execution error: nope (-1)\n")
    with pytest.raises(OSError, match="nope"):
        system.MacSystem().set_mute(True)
    fake_run.answer([system.OSASCRIPT, "-e", "get volume settings"], raises=subprocess.TimeoutExpired("osascript", 10))
    with pytest.raises(OSError, match="didn't answer"):
        system.MacSystem().get_volume()


# ------------------------------------------------------------------ system information
def test_system_info_like_windows(sysctl, monkeypatch):  # type: ignore[no-untyped-def]
    sysctl.answer(["/usr/bin/pmset", "-g", "batt"], BATT)
    sysctl.answer(["/usr/bin/vm_stat"], VM_STAT)
    monkeypatch.setattr(system.os, "getloadavg", lambda: (3.0, 2.0, 1.0))
    monkeypatch.setattr(system.os, "cpu_count", lambda: 12)
    monkeypatch.setattr(system.shutil, "disk_usage", lambda p: type("U", (), {"free": 50 * 2**30, "total": 500 * 2**30})())
    monkeypatch.setattr(system.time, "time", lambda: 1000 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5)
    assert system.MacSystem().system_info() == (
        "Battery: 83% (on battery); RAM: 12.0 of 16.0 GB used (75%); CPU: 25% (load 3.00 on 12 cores); "
        "Disk: 50 GB free of 500 GB; Uptime: 2d 3h 4m")


def test_system_info_on_a_desktop_mac_and_with_gaps(sysctl):  # type: ignore[no-untyped-def]
    sysctl.answer(["/usr/bin/pmset", "-g", "batt"], "Now drawing from 'AC Power'\n")
    sysctl.answer(["/usr/bin/vm_stat"], raises=OSError("no vm_stat"))
    info = system.system_info()
    assert info.startswith("Power: no battery (desktop); CPU: ") and "RAM" not in info and "Uptime: " in info


# ------------------------------------------------------------------ the lock screen
class _Bundle:
    pass


def test_lock_screen_uses_the_login_framework(monkeypatch):  # type: ignore[no-untyped-def]
    called = []
    fn = FakeModule(NSBundle=FakeModule(bundleWithPath_=lambda p: _Bundle()))
    monkeypatch.setattr(_objc, "FN", fn)

    def load(bundle, into, names):  # type: ignore[no-untyped-def]
        assert names == [("SACLockScreenImmediate", b"i")]
        into["SACLockScreenImmediate"] = lambda: called.append(1) or 0
    monkeypatch.setattr(_objc, "objc", FakeModule(loadBundleFunctions=load))
    assert system.MacSystem().lock_screen() is True and called == [1]


def test_lock_screen_falls_back_to_display_sleep_without_permissions(monkeypatch, fake_run):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "FN", FakeModule(NSBundle=FakeModule(bundleWithPath_=lambda p: None)))
    monkeypatch.setattr(_objc, "Q", FakeModule(CGPreflightPostEventAccess=lambda: False))
    fake_run.answer(["/usr/bin/pmset", "displaysleepnow"])
    assert system.lock_screen() is True
    assert fake_run.calls == [["/usr/bin/pmset", "displaysleepnow"]] and _objc.Q.called("CGEventPost") == []


def test_lock_screen_presses_ctrl_cmd_q_when_it_may(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "FN", FakeModule(NSBundle=FakeModule(bundleWithPath_=lambda p: None)))
    q = FakeModule(CGPreflightPostEventAccess=lambda: True, kCGEventFlagMaskControl=1 << 18,
                   kCGEventFlagMaskCommand=1 << 20, kCGHIDEventTap=0,
                   CGEventCreateKeyboardEvent=lambda src, key, down: ("key", key, down),
                   CGEventSetFlags=lambda ev, flags: None, CGEventPost=lambda tap, ev: None)
    monkeypatch.setattr(_objc, "Q", q)
    assert system.lock_screen() is True
    assert [args[1] for args in q.called("CGEventPost")] == [("key", 12, True), ("key", 12, False)]
    assert {args[1] for args in q.called("CGEventSetFlags")} == {(1 << 18) | (1 << 20)}


def test_lock_screen_reports_failure(monkeypatch, fake_run):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_objc, "FN", FakeModule(NSBundle=FakeModule(bundleWithPath_=lambda p: None)))
    monkeypatch.setattr(_objc, "Q", FakeModule(CGPreflightPostEventAccess=lambda: False))
    fake_run.answer(["/usr/bin/pmset", "displaysleepnow"], returncode=1)
    assert system.lock_screen() is False


# ------------------------------------------------------------------ media keys
class _NSEvent:
    def __init__(self, data1: int, flags: int) -> None:
        self.data1, self.flags = data1, flags

    def CGEvent(self):  # type: ignore[no-untyped-def]  # noqa: N802 - the AppKit name
        return ("cg", self.data1, self.flags)


def _media_fakes(monkeypatch, trusted: bool):  # type: ignore[no-untyped-def]
    ak = FakeModule(NSEventTypeSystemDefined=14, NSEvent=FakeModule(
        otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_=(
            lambda typ, loc, flags, ts, win, ctx, subtype, data1, data2: _NSEvent(data1, flags))))
    q = FakeModule(kCGHIDEventTap=0, CGEventPost=lambda tap, ev: None,
                   CGPreflightPostEventAccess=lambda: trusted)
    ax = FakeModule(AXIsProcessTrusted=lambda: trusted)
    for name, mod in (("AK", ak), ("Q", q), ("AX", ax)):
        monkeypatch.setattr(_objc, name, mod)
    return q


def test_media_keys_post_system_defined_events(monkeypatch):  # type: ignore[no-untyped-def]
    q = _media_fakes(monkeypatch, trusted=True)
    system.MacSystem().media_key("next")
    assert [a[1] for a in q.called("CGEventPost")] == [("cg", (17 << 16) | 0xA00, 0xA00), ("cg", (17 << 16) | 0xB00, 0xB00)]


def test_media_keys_need_accessibility(monkeypatch):  # type: ignore[no-untyped-def]
    q = _media_fakes(monkeypatch, trusted=False)
    with pytest.raises(PermissionNeeded, match="Accessibility"):
        system.MacSystem().media_key("play_pause")
    assert q.called("CGEventPost") == []


def test_there_is_no_stop_key():
    with pytest.raises(OSError, match="no stop media key"):
        system.MacSystem().media_key("stop")


# ------------------------------------------------------------------ live (read-only)
@pytest.mark.macos_live
def test_live_volume_settings_parse():  # runs osascript: only with PIXELCAT_MACOS_LIVE=1
    try:
        level, muted = system.MacSystem().get_volume()
    except OSError as exc:
        if str(exc) != system.NO_VOLUME:
            raise
        pytest.skip("this sound output has no volume ('missing value': HDMI, digital or a headless runner)")
    assert 0 <= level <= 100 and isinstance(muted, bool)


@pytest.mark.macos
def test_live_system_info():
    info = system.system_info()
    assert re.search(r"RAM: [\d.]+ of [\d.]+ GB used", info) and "Disk: " in info and "Uptime: " in info


@pytest.mark.macos
def test_live_the_lock_function_exists():  # loads the framework's function, never calls it
    from Foundation import NSBundle
    import objc

    found: dict = {}
    objc.loadBundleFunctions(NSBundle.bundleWithPath_(system.LOGIN_FRAMEWORK), found,
                             [("SACLockScreenImmediate", b"i")])
    assert "SACLockScreenImmediate" in found


@pytest.mark.macos_live
def test_live_volume_round_trip():
    s = system.MacSystem()
    level, muted = s.get_volume()
    target = level - 1 if level > 1 else 2
    try:
        assert s.set_volume(target) == target and s.get_volume() == (target, False)
        s.set_mute(True)
        assert s.get_volume()[1] is True
    finally:
        s.set_volume(level)
        s.set_mute(muted)
    assert s.get_volume() == (level, muted)


@pytest.mark.macos
def test_live_sysctl_reads_memory():
    assert int(hardware.sysctl("hw.memsize")) > 2**30
