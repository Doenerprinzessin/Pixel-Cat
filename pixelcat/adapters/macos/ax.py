"""AccessibilityPort on macOS: other apps' controls and text through the AX API (needs Accessibility).

Everything here only *checks* the permission (``AXIsProcessTrusted``), it never asks for it: a snapshot is part of
looking at the screen, which must not pop up anything.  Without it :meth:`MacAccessibility.snapshot` raises
:class:`~pixelcat.domain.PermissionNeeded` (the chat's helper carries on without an element list), the focused
element and text are None and a window's text is "".  The mouse and keyboard tools ask for the permission.

Rects are Quartz points (top-left of the main display, y down), like every rect of this adapter; AXPosition and
AXSize use the same space.  The controls are named in the words of the Windows UI Automation adapter (button,
checkbox, edit, ...), so the chat's helper reads the same list on both systems.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from . import _objc, permissions
from ...domain import Element, ElementKinds, PermissionNeeded, Snapshot, WindowId

log = logging.getLogger(__name__)

TIMEOUT_S = 2.0          # AXUIElementSetMessagingTimeout: a hung app answers nothing within this
MAX_NODES = 4000         # a snapshot visits at most this many elements ...
BUDGET_S = 3.0           # ... for at most this long (the agent gives up after 4 s)
OVERSAMPLE = 3           # collect this many times ``limit`` before sorting into reading order

# AX roles (and subroles, which win) in the UI Automation words of adapters/windows/uia.py
SUBROLES = {
    "AXSecureTextField": "edit", "AXSearchField": "edit", "AXTabButton": "tab item", "AXOutlineRow": "tree item",
    "AXSwitch": "checkbox", "AXToggle": "button",
}
ROLES = {
    "AXButton": "button", "AXMenuButton": "button", "AXDisclosureTriangle": "button", "AXColorWell": "button",
    "AXCheckBox": "checkbox", "AXRadioButton": "radio button",
    "AXPopUpButton": "combobox", "AXComboBox": "combobox",
    "AXTextField": "edit", "AXSearchField": "edit", "AXSecureTextField": "edit",
    "AXTextArea": "document", "AXWebArea": "document",
    "AXLink": "link", "AXMenuItem": "menu item", "AXMenuBarItem": "menu item",
    "AXSlider": "slider", "AXIncrementor": "spinner", "AXRow": "list item", "AXCell": "data item",
    "AXStaticText": "text", "AXHeading": "text",
    "AXImage": "image", "AXList": "list", "AXTable": "table", "AXOutline": "tree", "AXTabGroup": "tab",
    "AXToolbar": "toolbar", "AXGroup": "group", "AXScrollArea": "pane", "AXWindow": "window",
}
INTERACTIVE = frozenset({"button", "checkbox", "combobox", "edit", "link", "list item", "menu item", "radio button",
                         "slider", "spinner", "tab item", "tree item", "data item", "document"})
READABLE = INTERACTIVE | {"text"}
_KINDS = {"interactive": INTERACTIVE, "readable": READABLE}
_PASSWORD = frozenset({"AXSecureTextField"})
_TOGGLES = frozenset({"AXCheckBox", "AXRadioButton"})   # AXValue 0/1/2: on or off, not text
_TEXT_ROLES = frozenset({"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"})
_DOC_ROLES = frozenset({"AXTextArea", "AXStaticText"})

_timeout_lock = threading.Lock()
_timeout_set = False


# ------------------------------------------------------------------ AX calls
def _attr(el: Any, name: str) -> Any:
    """One attribute of an element; None if it has none (or the app didn't answer)."""
    try:
        err, value = _objc.AX.AXUIElementCopyAttributeValue(el, name, None)
    except Exception:  # noqa: BLE001 - a dead element or a value pyobjc can't convert
        log.debug("AX %s failed", name, exc_info=True)
        return None
    if err == _objc.AX.kAXErrorAPIDisabled:
        raise PermissionNeeded(permissions.ACCESSIBILITY, permissions.message(permissions.ACCESSIBILITY))
    return value if err == 0 else None


def _ax_value(value: Any, kind: str) -> Any:
    """The CGPoint / CGSize inside an AXValue; None if it is none."""
    if value is None:
        return None
    try:
        ok, out = _objc.AX.AXValueGetValue(value, getattr(_objc.AX, kind), None)
    except Exception:  # noqa: BLE001 - not an AXValue
        return None
    return out if ok else None


def _rect(el: Any) -> tuple[int, int, int, int] | None:
    pos = _ax_value(_attr(el, "AXPosition"), "kAXValueCGPointType")
    size = _ax_value(_attr(el, "AXSize"), "kAXValueCGSizeType")
    if pos is None or size is None:
        return None
    x, y = round(pos.x), round(pos.y)
    return (x, y, x + round(size.width), y + round(size.height))


def _text(value: Any) -> str:
    """An attribute as text: strings as they are, numbers as digits, anything else ""."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return ""


def _kind(role: str, subrole: str) -> str:
    return SUBROLES.get(subrole) or ROLES.get(role, "element")


def _name(el: Any, kind: str) -> str:
    for attr in ("AXTitle", "AXDescription"):
        name = _text(_attr(el, attr))
        if name:
            return name
    if kind == "text":
        return _text(_attr(el, "AXValue"))
    title_el = _attr(el, "AXTitleUIElement")
    if title_el is not None:
        name = _text(_attr(title_el, "AXValue")) or _text(_attr(title_el, "AXTitle"))
        if name:
            return name
    return _text(_attr(el, "AXPlaceholderValue")) or _text(_attr(el, "AXHelp"))


def _element(el: Any, role: str, subrole: str, rect: tuple[int, int, int, int], focused: bool | None = None) -> Element:
    kind = _kind(role, subrole)
    pw = role in _PASSWORD or subrole in _PASSWORD
    raw = _attr(el, "AXValue")
    checked: bool | None = None
    toggle = role in _TOGGLES
    if toggle and kind in ("checkbox", "radio button") and isinstance(raw, (int, float)) and not isinstance(raw, bool):
        checked = {0: False, 1: True}.get(int(raw))       # 2 = mixed
    value = "" if pw or toggle or kind == "text" else _text(raw)
    enabled = _attr(el, "AXEnabled")
    if focused is None:
        focused = bool(_attr(el, "AXFocused"))
    return Element(0, kind, _name(el, kind), rect, value, enabled is not False, focused, pw, checked)


def _set_timeout() -> None:
    """A messaging timeout for every AX call of this process (once)."""
    global _timeout_set
    with _timeout_lock:
        if _timeout_set:
            return
        _timeout_set = True
    try:
        _objc.AX.AXUIElementSetMessagingTimeout(_objc.AX.AXUIElementCreateSystemWide(), TIMEOUT_S)
    except Exception:  # noqa: BLE001 - the default timeout (6 s) then
        log.debug("AXUIElementSetMessagingTimeout failed", exc_info=True)


def _cg_window(win: WindowId) -> tuple[int, tuple[int, int, int, int]] | None:
    """(owner pid, bounds) of a window from the window server (no permission needed)."""
    try:
        infos = _objc.Q.CGWindowListCopyWindowInfo(_objc.Q.kCGWindowListOptionIncludingWindow, int(win)) or []
    except Exception:  # noqa: BLE001
        log.debug("CGWindowListCopyWindowInfo failed", exc_info=True)
        return None
    for info in infos:
        if int(info.get("kCGWindowNumber", 0)) != int(win):
            continue
        b = info.get("kCGWindowBounds") or {}
        x, y = round(b.get("X", 0)), round(b.get("Y", 0))
        return int(info.get("kCGWindowOwnerPID", 0)), (x, y, x + round(b.get("Width", 0)), y + round(b.get("Height", 0)))
    return None


def _ax_window(win: WindowId) -> tuple[Any, tuple[int, int, int, int]]:
    """The AX element of a window (the app's AXWindow whose frame matches the window server's) and its bounds.
    There is no public CGWindowID -> AXUIElement mapping, so the frames decide."""
    if _objc.is_own(win):
        raise OSError("that is one of the cat's own windows")
    found = _cg_window(win)
    if found is None:
        raise OSError("that window is gone")
    pid, bounds = found
    _set_timeout()
    app = _objc.AX.AXUIElementCreateApplication(pid)
    try:
        _objc.AX.AXUIElementSetMessagingTimeout(app, TIMEOUT_S)
    except Exception:  # noqa: BLE001
        pass
    best, best_d = None, None
    for w in _attr(app, "AXWindows") or []:
        r = _rect(w)
        if r is None:
            continue
        d = sum(abs(a - b) for a, b in zip(r, bounds))
        if best_d is None or d < best_d:
            best, best_d = w, d
    if best is None or best_d > 8:      # no exact match: the app's focused (or main) window if it is close
        for attr in ("AXFocusedWindow", "AXMainWindow"):
            w = _attr(app, attr)
            r = _rect(w) if w is not None else None
            if r is not None and sum(abs(a - b) for a, b in zip(r, bounds)) <= 40:
                best = w
                break
        else:
            if best is None:
                raise OSError("can't find that window's controls (the app doesn't show them to Accessibility)")
    return best, bounds


def _trusted_or_raise() -> None:
    if not permissions.accessibility():   # checked only - looking must never pop up a prompt
        raise PermissionNeeded(permissions.ACCESSIBILITY, permissions.message(permissions.ACCESSIBILITY))


def _walk(root: Any, max_nodes: int = MAX_NODES, budget_s: float = BUDGET_S):  # type: ignore[no-untyped-def]
    """(element, role, subrole) breadth-first below ``root``, bounded by count and time."""
    t_end = time.perf_counter() + budget_s
    queue: deque[Any] = deque(_attr(root, "AXChildren") or [])
    seen = 0
    while queue and seen < max_nodes and time.perf_counter() < t_end:
        el = queue.popleft()
        seen += 1
        role = _text(_attr(el, "AXRole"))
        subrole = _text(_attr(el, "AXSubrole"))
        yield el, role, subrole
        queue.extend(_attr(el, "AXChildren") or [])


class MacAccessibility:
    def snapshot(self, win: WindowId, kinds: ElementKinds = "interactive", limit: int = 150) -> Snapshot:
        _trusted_or_raise()
        t0 = time.perf_counter()
        root, wr = _ax_window(win)
        wanted = _KINDS[kinds]
        found: list[Element] = []
        for el, role, subrole in _walk(root):
            if _kind(role, subrole) not in wanted:
                continue
            rect = _rect(el)
            if rect is None or rect[2] - rect[0] < 2 or rect[3] - rect[1] < 2:
                continue
            if rect[2] <= wr[0] or rect[0] >= wr[2] or rect[3] <= wr[1] or rect[1] >= wr[3]:
                continue
            found.append(_element(el, role, subrole, rect))
            if len(found) >= limit * OVERSAMPLE:
                break
        found.sort(key=lambda e: (e.rect[1] // 12, e.rect[0]))   # rows of ~12 pt, then left to right
        out: list[Element] = []
        for e in found:
            if not e.name and not e.value and e.kind in ("text", "list item", "data item"):
                continue
            e.id = len(out) + 1
            out.append(e)
            if len(out) >= limit:
                break
        snap = Snapshot(win, wr, out, next((e for e in out if e.focused), None))
        snap.took_ms = (time.perf_counter() - t0) * 1000
        return snap

    @staticmethod
    def _focused() -> Any:
        if not permissions.accessibility():
            return None
        _set_timeout()
        return _attr(_objc.AX.AXUIElementCreateSystemWide(), "AXFocusedUIElement")

    def focused_element(self) -> Element | None:
        try:
            el = self._focused()
            if el is None:
                return None
            role, subrole = _text(_attr(el, "AXRole")), _text(_attr(el, "AXSubrole"))
            return _element(el, role, subrole, _rect(el) or (0, 0, 0, 0), focused=True)
        except OSError:
            return None

    def focused_text(self, max_chars: int = 50000) -> str | None:
        try:
            el = self._focused()
            if el is None:
                return None
            role, subrole = _text(_attr(el, "AXRole")), _text(_attr(el, "AXSubrole"))
            if role in _PASSWORD or subrole in _PASSWORD:
                return None
            value = _attr(el, "AXValue")
            if role not in _TEXT_ROLES and not isinstance(value, str):
                return None
            return _text(value)[:max_chars]
        except OSError:
            return None

    def document_text(self, win: WindowId, max_chars: int = 8000) -> str:
        if not permissions.accessibility():
            return ""
        try:
            root, _wr = _ax_window(win)
            parts: list[str] = []
            size = 0
            for el, role, subrole in _walk(root):
                if role not in _DOC_ROLES or subrole in _PASSWORD:
                    continue
                text = _text(_attr(el, "AXValue")).strip()
                if text:
                    parts.append(text)
                    size += len(text) + 1
                    if size >= max_chars:
                        break
            return "\n".join(parts)[:max_chars]
        except OSError:
            log.debug("document_text failed", exc_info=True)
            return ""
