"""AccessibilityPort on macOS: other apps' controls and text through the AX API (needs Accessibility).

Skeleton: no controls, no text.
"""
from __future__ import annotations

from ...domain import Element, ElementKinds, Snapshot, WindowId


class MacAccessibility:
    def snapshot(self, win: WindowId, kinds: ElementKinds = "interactive", limit: int = 150) -> Snapshot:
        return Snapshot(hwnd=win, window_rect=(0, 0, 0, 0))

    def focused_element(self) -> Element | None:
        return None

    def focused_text(self, max_chars: int = 50000) -> str | None:
        return None

    def document_text(self, win: WindowId, max_chars: int = 8000) -> str:
        return ""
