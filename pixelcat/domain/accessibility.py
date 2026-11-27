"""Accessibility: the controls and text of another app's window (plain data, no platform calls)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ElementKinds = Literal["interactive", "readable"]   # which controls a snapshot lists


@dataclass
class Element:
    id: int
    kind: str
    name: str
    rect: tuple[int, int, int, int]       # physical screen pixels
    value: str = ""
    enabled: bool = True
    focused: bool = False
    password: bool = False
    checked: bool | None = None           # checkboxes / toggle buttons; None = not a toggle

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.rect
        return ((l + r) // 2, (t + b) // 2)

    def describe(self, origin: tuple[int, int] = (0, 0), scale: float = 1.0) -> str:
        cx, cy = self.center
        x, y = int((cx - origin[0]) * scale), int((cy - origin[1]) * scale)
        name = self.name.replace("\n", " ").strip()
        name = name if len(name) <= 60 else name[:57] + "..."
        bits = [f"[{self.id}] {self.kind}"]
        if name:
            bits.append(f"'{name}'")
        if self.value and not self.password and self.kind not in ("document",):
            v = self.value.replace("\n", " ")
            bits.append(f"value='{v[:50] + '...' if len(v) > 50 else v}'")
        if self.checked is not None:
            bits.append("(checked)" if self.checked else "(unchecked)")
        if self.focused:
            bits.append("(focused)")
        if not self.enabled:
            bits.append("(disabled)")
        if self.password:
            bits.append("(password field)")
        bits.append(f"at {x},{y}")
        return " ".join(bits)


@dataclass
class Snapshot:
    hwnd: int
    window_rect: tuple[int, int, int, int]
    elements: list[Element] = field(default_factory=list)
    focused: Element | None = None
    took_ms: float = 0.0
