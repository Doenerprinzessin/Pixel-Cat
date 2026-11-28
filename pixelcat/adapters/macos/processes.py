"""ProcessPort on macOS: running programs (NSRunningApplication, ``ps``), ending them with signals.

Skeleton: nothing can be ended yet.
"""
from __future__ import annotations

from ...domain import KillPlan

NO_ADMIN = "ending that needs admin rights on macOS - I won't do that"


class MacProcesses:
    def process_exe(self, pid: int) -> str:
        return ""

    def plan_force_end(self, target: str, own_pid: int) -> KillPlan:
        raise ValueError("ending apps isn't supported on this Mac yet")

    def force_end(self, plan: KillPlan) -> tuple[list[int], list[int]]:
        return ([], [])

    def force_end_elevated(self, pids: list[int]) -> tuple[bool, str]:
        return (False, NO_ADMIN)

    def alive(self, pids: list[int]) -> list[int]:
        return []

    def is_elevated(self, pid: int) -> bool:
        return False

    def self_elevated(self) -> bool:
        return False

    def uac_prompt_open(self) -> bool:
        return False
