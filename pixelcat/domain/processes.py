"""Processes as the chat's force-end tool sees them (plain data, no platform calls)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    exe: str


@dataclass
class KillPlan:
    label: str               # what the user will see, e.g. "chrome.exe (14 processes)"
    pids: list[int]
    exes: list[str]
