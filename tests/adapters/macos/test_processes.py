"""pixelcat.adapters.macos.processes: ``/force end`` plans like Task Manager's "End task", never the core of
macOS or the cat's own family; ending is a SIGKILL that's checked first (fakes only: nothing is ever killed)."""
from __future__ import annotations

import os
import signal

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, processes
from pixelcat.domain import KillPlan

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HELPER = "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"
PS = f"""\
    1     0 /sbin/launchd
  100     1 /System/Library/CoreServices/WindowServer.app/Contents/MacOS/WindowServer
  110     1 /System/Library/CoreServices/Finder.app/Contents/MacOS/Finder
  120     1 /System/Library/CoreServices/Dock.app/Contents/MacOS/Dock
  130     1 /System/Library/Frameworks/Security.framework/XPCServices/com.apple.CodeSigningHelper.xpc/Contents/MacOS/com.apple.CodeSigningHelper
  200     1 {CHROME}
  201   200 {HELPER}
  202   200 {HELPER} (Renderer)
  203   201 {HELPER}
  210   200 /Applications/Slack.app/Contents/MacOS/Slack
  300     1 /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal
  301   300 login
  302   301 -zsh
  303   302 /opt/homebrew/bin/python3.13
  400     1 /Applications/Microsoft Teams.app/Contents/MacOS/MSTeams
  401   400 /Applications/Microsoft Teams.app/Contents/Helpers/Microsoft Teams WebView.app/Contents/MacOS/Microsoft Teams WebView
  500     1 /Applications/Visual Studio Code.app/Contents/MacOS/Code
  501   500 /Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/Code Helper
  502   501 /bin/zsh
  600     0 kernel_task
"""
# (pid, Dock name, regular app)
APPS = [(110, "Finder", True), (200, "Google Chrome", True), (210, "Slack", True), (300, "Terminal", True),
        (400, "Microsoft Teams", True), (500, "Code", True)]


class _App:
    def __init__(self, pid: int, name: str, regular: bool) -> None:
        self.pid, self.name, self.regular = pid, name, regular

    def processIdentifier(self) -> int:  # noqa: N802 - the AppKit names
        return self.pid

    def localizedName(self) -> str:  # noqa: N802
        return self.name

    def activationPolicy(self) -> int:  # noqa: N802
        return 0 if self.regular else 1


@pytest.fixture
def mac(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    fake_run.answer(["/bin/ps", "-axo", "pid=,ppid=,comm="], PS)
    ws = FakeModule(runningApplications=lambda: [_App(*a) for a in APPS])
    monkeypatch.setattr(_objc, "AK", FakeModule(NSApplicationActivationPolicyRegular=0,
                                                NSWorkspace=FakeModule(sharedWorkspace=lambda: ws)))
    return fake_run


def _plan(target: str, own_pid: int = 303) -> KillPlan:
    return processes.MacProcesses().plan_force_end(target, own_pid)


def test_ps_lines_with_spaces_and_bare_names(mac):  # type: ignore[no-untyped-def]
    procs = processes.list_processes()
    assert procs[0] == processes.Proc(1, 0, "launchd")
    by_pid = {p.pid: p.exe for p in procs}
    assert by_pid[202] == "Google Chrome Helper (Renderer)" and by_pid[301] == "login" and by_pid[600] == "kernel_task"


def test_an_app_ends_with_its_helpers_but_not_other_apps(mac):  # type: ignore[no-untyped-def]
    plan = _plan("chrome")      # alias -> "google chrome"
    assert plan.pids == [200, 201, 202, 203]        # not Slack (its own app), even though Chrome started it
    assert plan.exes == ["Google Chrome", "Google Chrome Helper", "Google Chrome Helper (Renderer)"]
    assert plan.label == "Google Chrome, Google Chrome Helper, Google Chrome Helper (Renderer) (4 processes)"


def test_names_match_without_cutting_letters(mac):  # type: ignore[no-untyped-def]
    assert _plan("Slack").pids == [210] and _plan("slack").label == "Slack (1 process)"
    assert _plan('"Slack.app"').pids == [210]


def test_the_dock_name_finds_apps_whose_program_has_another_name(mac):  # type: ignore[no-untyped-def]
    assert _plan("teams").pids == [400, 401]        # Microsoft Teams runs as MSTeams


def test_a_part_of_a_name_never_reaches_macos_itself(mac):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="no running app matches 'signinghelper'"):
        _plan("signinghelper")
    assert _plan("chro").pids == [200, 201, 202, 203]   # a part of an app's name is fine


@pytest.mark.parametrize("target, name", [("finder", "Finder"), ("Dock", "Dock"), ("windowserver", "WindowServer"),
                                          ("launchd", "launchd"), ("kernel_task", "kernel_task")])
def test_the_core_of_macos_is_refused(mac, target, name):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match=f"^I won't end {name} - it's a core part of macOS$"):
        _plan(target)


def test_the_cat_and_what_started_it_are_never_ended(mac):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="^nothing I'm allowed to end matches 'terminal'$"):
        _plan("terminal")                    # the cat (303) runs in this Terminal
    with pytest.raises(ValueError, match="nothing I'm allowed to end"):
        _plan("python3.13")
    plan = _plan("code")                     # another app's terminal shell goes along with it
    assert plan.pids == [500, 501, 502]
    assert _plan("terminal", own_pid=502).pids == [300, 301, 302, 303]


def test_nothing_matches(mac):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="no running app matches 'photoshop'"):
        _plan("photoshop")
    with pytest.raises(ValueError, match="tell me which app"):
        _plan("  ")


def test_force_end_kills_only_what_still_runs_the_planned_program(mac, monkeypatch):  # type: ignore[no-untyped-def]
    sent: list[tuple[int, int]] = []
    gone: set[int] = set()

    def kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid in gone:
                raise ProcessLookupError(pid)
            return
        sent.append((pid, sig))
        if pid == 202:
            raise PermissionError(pid)
        if pid == 203:
            raise ProcessLookupError(pid)
        gone.add(pid)
    monkeypatch.setattr(processes.os, "kill", kill)
    plan = KillPlan("x", [200, 201, 202, 203, 210, 999], ["Google Chrome", "Google Chrome Helper",
                                                         "Google Chrome Helper (Renderer)"])
    ended, denied = processes.MacProcesses().force_end(plan)
    assert (ended, denied) == ([200, 201], [202])
    assert all(sig == signal.SIGKILL for _pid, sig in sent)
    assert 210 not in [pid for pid, _sig in sent]       # pid now runs Slack: never touched
    assert processes.MacProcesses().alive([200, 202]) == [202]


def test_force_end_without_ps_ends_nothing(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    fake_run.answer(["/bin/ps"], raises=OSError("no ps"))
    monkeypatch.setattr(processes.os, "kill", lambda pid, sig: pytest.fail("killed without checking"))
    assert processes.force_end(KillPlan("x", [200], ["Google Chrome"])) == ([], [])


def test_alive_and_the_admin_answers():
    p = processes.MacProcesses()
    assert p.alive([os.getpid(), 999_999_9]) == [os.getpid()]
    assert p.force_end_elevated([1]) == (False, processes.NO_ADMIN)
    assert p.is_elevated(1) is False and p.uac_prompt_open() is False
    assert p.self_elevated() is (os.geteuid() == 0)


class _Url:
    def lastPathComponent(self) -> str:  # noqa: N802
        return "Safari"


def test_process_exe(fake_run, monkeypatch):  # type: ignore[no-untyped-def]
    apps = {7: FakeModule(executableURL=lambda: _Url())}
    monkeypatch.setattr(_objc, "AK", FakeModule(NSRunningApplication=FakeModule(
        runningApplicationWithProcessIdentifier_=lambda pid: apps.get(pid))))
    fake_run.answer(["/bin/ps", "-o", "comm=", "-p", "8"], "/opt/homebrew/bin/python3.13\n")
    fake_run.answer(["/bin/ps", "-o", "comm=", "-p", "9"], "", returncode=1)
    p = processes.MacProcesses()
    assert (p.process_exe(7), p.process_exe(8), p.process_exe(9)) == ("Safari", "python3.13", "")


# ------------------------------------------------------------------ live (read-only: plans, never ends)
@pytest.mark.macos
def test_live_ps_and_a_plan_that_refuses_finder():
    procs = processes.list_processes()
    assert any(p.pid == 1 and p.exe == "launchd" for p in procs) and len(procs) > 20
    assert processes.process_exe(os.getpid())
    with pytest.raises(ValueError, match="core part of macOS"):
        processes.plan_force_end("finder", os.getpid())
