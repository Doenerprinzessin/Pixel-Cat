"""What this Mac can do for the local AI: memory, cores, CPU, free disk (sysctl, shutil.disk_usage).

Skeleton: the domain's defaults.
"""
from __future__ import annotations

from ...domain import Hardware


def detect(fresh: bool = False) -> Hardware:
    """This Mac's hardware (looked at once unless ``fresh``)."""
    return Hardware()
