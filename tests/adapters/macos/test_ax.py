"""pixelcat.adapters.macos.ax: a fake AX tree read in the UI Automation words; without Accessibility nothing asks."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from adapters.macos.fakes import FakeModule
from pixelcat.adapters.macos import _objc, ax
from pixelcat.domain import PermissionNeeded

WIN = 77
PID = 4242
BOUNDS = (100, 50, 500, 350)     # the window server's frame of WIN


class Node:
    def __init__(self, role: str = "", rect: tuple[int, int, int, int] | None = None, children: list | None = None,
                 **attrs: Any) -> None:
        self.attrs = dict(attrs)
        if role:
            self.attrs["AXRole"] = role
        if rect is not None:
            self.attrs["AXPosition"] = SimpleNamespace(x=float(rect[0]), y=float(rect[1]))
            self.attrs["AXSize"] = SimpleNamespace(width=float(rect[2] - rect[0]), height=float(rect[3] - rect[1]))
        if children is not None:
            self.attrs["AXChildren"] = children


def _fake_ax(monkeypatch: pytest.MonkeyPatch, app: Node, trusted: bool = True, focused: Node | None = None,
             cg: list | None = None) -> FakeModule:
    system_wide = Node(AXFocusedUIElement=focused) if focused is not None else Node()

    def copy(el: Node, name: str, _out: None) -> tuple[int, Any]:
        return (0, el.attrs[name]) if name in el.attrs else (-25205, None)

    fake = FakeModule(
        AXIsProcessTrusted=lambda: trusted, AXIsProcessTrustedWithOptions=lambda opts: trusted,
        kAXTrustedCheckOptionPrompt="prompt", kAXErrorAPIDisabled=-25211,
        kAXValueCGPointType="point", kAXValueCGSizeType="size",
        AXUIElementCopyAttributeValue=copy, AXValueGetValue=lambda v, kind, _out: (True, v),
        AXUIElementCreateApplication=lambda pid: app, AXUIElementCreateSystemWide=lambda: system_wide,
        AXUIElementSetMessagingTimeout=lambda el, s: 0)
    monkeypatch.setattr(_objc, "AX", fake)
    l, t, r, b = BOUNDS
    infos = cg if cg is not None else [{"kCGWindowNumber": WIN, "kCGWindowOwnerPID": PID,
                                        "kCGWindowBounds": {"X": l, "Y": t, "Width": r - l, "Height": b - t}}]
    monkeypatch.setattr(_objc, "Q", FakeModule(kCGWindowListOptionIncludingWindow=8,
                                               CGWindowListCopyWindowInfo=lambda opt, win: infos))
    monkeypatch.setattr(ax, "_timeout_set", False)
    monkeypatch.delenv("PIXELCAT_NO_PROMPTS", raising=False)      # a request would really be made: none may be
    return fake


def _app() -> Node:
    other = Node("AXWindow", (0, 0, 300, 200), [Node("AXButton", (10, 10, 50, 30), AXTitle="Wrong window")])
    title = Node("AXStaticText", (110, 60, 160, 80), AXValue="Name:")
    tree = [
        Node("AXGroup", (100, 50, 500, 350), [
            Node("AXButton", (300, 300, 380, 330), AXTitle="OK", AXEnabled=True, AXFocused=True),
            Node("AXButton", (200, 300, 280, 330), AXDescription="Cancel", AXEnabled=False),
            title,
            Node("AXTextField", (170, 60, 400, 80), AXTitleUIElement=title, AXValue="Mia"),
            Node("AXTextField", (170, 90, 400, 110), AXSubrole="AXSecureTextField", AXTitle="Password",
                 AXValue="hunter2"),
            Node("AXCheckBox", (110, 120, 200, 140), AXTitle="Remember me", AXValue=1),
            Node("AXRadioButton", (110, 150, 200, 170), AXSubrole="AXTabButton", AXTitle="General", AXValue=0),
            Node("AXPopUpButton", (110, 180, 250, 200), AXTitle="Size", AXValue="Large"),
            Node("AXStaticText", (110, 210, 200, 230), AXValue=""),
            Node("AXButton", (900, 900, 950, 950), AXTitle="Outside the window"),
            Node("AXButton", (110, 240, 111, 260), AXTitle="Too thin"),
            Node("AXImage", (300, 120, 400, 200), AXDescription="logo"),
        ]),
    ]
    main = Node("AXWindow", BOUNDS, tree)
    return Node(AXWindows=[other, main])


def test_snapshot_reads_the_matching_window_in_uia_words_and_reading_order(monkeypatch):  # type: ignore[no-untyped-def]
    fake = _fake_ax(monkeypatch, _app())
    snap = ax.MacAccessibility().snapshot(WIN)
    assert snap.hwnd == WIN and snap.window_rect == BOUNDS
    got = [(e.id, e.kind, e.name, e.value, e.rect, e.enabled, e.focused, e.password, e.checked) for e in snap.elements]
    assert got == [
        (1, "edit", "Name:", "Mia", (170, 60, 400, 80), True, False, False, None),
        (2, "edit", "Password", "", (170, 90, 400, 110), True, False, True, None),
        (3, "checkbox", "Remember me", "", (110, 120, 200, 140), True, False, False, True),
        (4, "tab item", "General", "", (110, 150, 200, 170), True, False, False, None),
        (5, "combobox", "Size", "Large", (110, 180, 250, 200), True, False, False, None),
        (6, "button", "Cancel", "", (200, 300, 280, 330), False, False, False, None),
        (7, "button", "OK", "", (300, 300, 380, 330), True, True, False, None),
    ]
    assert snap.focused is snap.elements[6]
    assert fake.called("AXUIElementCreateApplication") == [(PID,)]
    assert fake.called("AXUIElementSetMessagingTimeout")[0][1] == ax.TIMEOUT_S
    assert fake.called("AXIsProcessTrustedWithOptions") == []


def test_readable_snapshots_add_texts_and_limit_cuts_in_reading_order(monkeypatch):  # type: ignore[no-untyped-def]
    _fake_ax(monkeypatch, _app())
    snap = ax.MacAccessibility().snapshot(WIN, "readable", limit=3)
    assert [(e.id, e.kind, e.name) for e in snap.elements] == [(1, "text", "Name:"), (2, "edit", "Name:"),
                                                                (3, "edit", "Password")]


def test_without_accessibility_nothing_is_read_and_nothing_asks(monkeypatch):  # type: ignore[no-untyped-def]
    fake = _fake_ax(monkeypatch, _app(), trusted=False, focused=Node("AXTextArea", AXValue="secret"))
    acc = ax.MacAccessibility()
    with pytest.raises(PermissionNeeded) as err:
        acc.snapshot(WIN)
    assert err.value.permission == "accessibility" and "Accessibility" in str(err.value)
    assert acc.focused_element() is None and acc.focused_text() is None and acc.document_text(WIN) == ""
    assert {name for name, _args in fake.calls} == {"AXIsProcessTrusted"}


def test_own_and_vanished_windows_raise_oserror(monkeypatch):  # type: ignore[no-untyped-def]
    _fake_ax(monkeypatch, _app(), cg=[])
    acc = ax.MacAccessibility()
    with pytest.raises(OSError, match="gone"):
        acc.snapshot(WIN)
    with pytest.raises(OSError, match="own"):
        acc.snapshot(_objc.OWN_ID_MIN + 5)
    assert acc.document_text(WIN) == ""


def test_a_window_without_an_exact_ax_match_uses_the_focused_window_if_close(monkeypatch):  # type: ignore[no-untyped-def]
    near = Node("AXWindow", (102, 52, 502, 352), [Node("AXButton", (200, 200, 260, 220), AXTitle="Go")])
    app = Node(AXWindows=[Node("AXWindow", (0, 0, 50, 50), [])], AXFocusedWindow=near)
    _fake_ax(monkeypatch, app)
    assert [e.name for e in ax.MacAccessibility().snapshot(WIN).elements] == ["Go"]


def test_focused_element_and_text(monkeypatch):  # type: ignore[no-untyped-def]
    field = Node("AXTextArea", (10, 20, 110, 220), AXValue="Dear cat,\nhello", AXTitle="Body")
    _fake_ax(monkeypatch, _app(), focused=field)
    acc = ax.MacAccessibility()
    el = acc.focused_element()
    assert el is not None and (el.kind, el.name, el.rect, el.focused) == ("document", "Body", (10, 20, 110, 220), True)
    assert acc.focused_text() == "Dear cat,\nhello" and acc.focused_text(max_chars=4) == "Dear"


def test_focused_text_of_a_password_field_or_a_button_is_none(monkeypatch):  # type: ignore[no-untyped-def]
    _fake_ax(monkeypatch, _app(), focused=Node("AXTextField", AXSubrole="AXSecureTextField", AXValue="pw"))
    assert ax.MacAccessibility().focused_text() is None
    _fake_ax(monkeypatch, _app(), focused=Node("AXButton", AXTitle="OK"))
    assert ax.MacAccessibility().focused_text() is None
    assert ax.MacAccessibility().focused_element().kind == "button"   # type: ignore[union-attr]


def test_document_text_joins_texts_and_documents_up_to_max_chars(monkeypatch):  # type: ignore[no-untyped-def]
    main = Node("AXWindow", BOUNDS, [Node("AXScrollArea", None, [
        Node("AXTextArea", AXValue="Line one\nLine two"), Node("AXStaticText", AXValue=" Footer "),
        Node("AXTextField", AXValue="not a document")])])
    _fake_ax(monkeypatch, Node(AXWindows=[main]))
    acc = ax.MacAccessibility()
    assert acc.document_text(WIN) == "Line one\nLine two\nFooter"
    assert acc.document_text(WIN, max_chars=8) == "Line one"


@pytest.mark.macos
def test_the_real_trust_check_never_prompts():
    from pixelcat.adapters.macos import permissions

    assert isinstance(permissions.accessibility(), bool)       # AXIsProcessTrusted only


@pytest.mark.macos
def test_real_ax_values_unwrap_to_points_and_sizes():
    import ApplicationServices as AX
    import Quartz

    pos = AX.AXValueCreate(AX.kAXValueCGPointType, Quartz.CGPointMake(3.4, 7.0))
    size = AX.AXValueCreate(AX.kAXValueCGSizeType, Quartz.CGSizeMake(10.0, 20.6))
    p, s = ax._ax_value(pos, "kAXValueCGPointType"), ax._ax_value(size, "kAXValueCGSizeType")
    assert (p.x, p.y, s.width, s.height) == (3.4, 7.0, 10.0, 20.6)
    assert ax._ax_value(None, "kAXValueCGPointType") is None
