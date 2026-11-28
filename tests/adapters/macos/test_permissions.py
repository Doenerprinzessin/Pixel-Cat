"""pixelcat.adapters.macos.permissions: checks never prompt, requests once per run, clear messages."""
from __future__ import annotations

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, permissions
from pixelcat.domain import PermissionNeeded


@pytest.fixture
def tcc(monkeypatch):  # type: ignore[no-untyped-def]
    """Fake AX and Quartz with nothing granted; ``tcc.grant(kind)`` grants one."""
    granted: set[str] = set()
    ax = FakeModule(AXIsProcessTrusted=lambda: "accessibility" in granted, kAXTrustedCheckOptionPrompt="prompt",
                    AXIsProcessTrustedWithOptions=lambda opts: "accessibility" in granted)
    q = FakeModule(CGPreflightScreenCaptureAccess=lambda: "screen_recording" in granted,
                   CGPreflightPostEventAccess=lambda: "accessibility" in granted,
                   CGPreflightListenEventAccess=lambda: "input_monitoring" in granted,
                   CGRequestScreenCaptureAccess=lambda: False, CGRequestListenEventAccess=lambda: False)
    monkeypatch.setattr(_objc, "AX", ax)
    monkeypatch.setattr(_objc, "Q", q)
    monkeypatch.delenv("PIXELCAT_NO_PROMPTS")
    ax.grant = granted.add       # (stored as a name of the fake)
    return ax


def _prompts(ax: FakeModule) -> list:
    return [c for c in ax.calls + _objc.Q.calls if "Request" in c[0] or c[0] == "AXIsProcessTrustedWithOptions"]


def test_the_checks_never_prompt(tcc):  # type: ignore[no-untyped-def]
    assert not (permissions.accessibility() or permissions.screen_recording() or permissions.post_events()
                or permissions.listen_events())
    tcc.grant("screen_recording")
    assert permissions.screen_recording() and permissions.granted("screen_recording")
    assert _prompts(tcc) == []


def test_a_check_without_pyobjc_is_not_granted():  # the conftest's stubs fail on use
    assert permissions.accessibility() is False and permissions.screen_recording() is False


def test_need_asks_once_then_opens_the_pane_once_then_only_raises(tcc, fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer(["/usr/bin/open"])
    for _ in range(3):
        with pytest.raises(PermissionNeeded) as err:
            permissions.need("accessibility")
        assert err.value.permission == "accessibility" and isinstance(err.value, OSError)
    assert tcc.called("AXIsProcessTrustedWithOptions") == [({"prompt": True},)]
    assert fake_run.calls == [["/usr/bin/open",
                               "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"]]


def test_need_passes_once_granted(tcc):  # type: ignore[no-untyped-def]
    tcc.grant("accessibility")
    permissions.need("accessibility")
    assert _prompts(tcc) == []


def test_the_pane_falls_back_to_the_new_settings_address(tcc, fake_run):  # type: ignore[no-untyped-def]
    fake_run.answer(["/usr/bin/open"], returncode=1)
    fake_run.answer(["/usr/bin/open", "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?"
                     "Privacy_ScreenCapture"])
    assert permissions.open_pane("screen_recording") is True
    assert len(fake_run.calls) == 2
    assert permissions.open_pane("screen_recording") is False and len(fake_run.calls) == 2     # once per run


def test_no_prompts_turns_requests_and_panes_off(tcc, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PIXELCAT_NO_PROMPTS", "1")       # (_objc.run still fails the test if used)
    for _ in range(2):
        with pytest.raises(PermissionNeeded):
            permissions.need("screen_recording")
    assert permissions.open_pane("accessibility") is False
    assert _prompts(tcc) == []


@pytest.mark.parametrize("kind", permissions.KINDS)
def test_the_message_names_the_settings_pane(kind, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(permissions._platform, "mac_ver", lambda: ("26.1", ("", "", ""), "arm64"))
    text = permissions.message(kind)
    assert text.startswith("macOS hasn't allowed Pixel Cat to ")
    assert f"System Settings → Privacy & Security → {permissions.pane_name(kind)} and turn on " in text
    assert "Terminal" in text and "Python" in text                      # (run from a terminal: who to turn on)
    assert text.endswith("then quit and restart Pixel Cat." if kind == "screen_recording" else "then try again.")


def test_the_screen_recording_pane_name_follows_the_macos_version(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(permissions._platform, "mac_ver", lambda: ("14.6", ("", "", ""), "arm64"))
    assert permissions.pane_name("screen_recording") == "Screen Recording"
    monkeypatch.setattr(permissions._platform, "mac_ver", lambda: ("15.0", ("", "", ""), "arm64"))
    assert permissions.pane_name("screen_recording") == "Screen & System Audio Recording"


def test_a_bundled_cat_names_itself(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(permissions.sys, "frozen", True, raising=False)
    text = permissions.message("accessibility")
    assert "turn on Pixel Cat, then try again." in text and "Terminal" not in text


@pytest.mark.macos
def test_the_real_checks_answer_without_prompting():
    # read-only preflight calls; PIXELCAT_NO_PROMPTS=1 keeps need() from asking
    for check in (permissions.accessibility, permissions.screen_recording, permissions.post_events,
                  permissions.listen_events):
        assert isinstance(check(), bool)
