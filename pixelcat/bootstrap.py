"""The composition root: the only place that picks the platform adapters.

``main`` builds the :class:`~pixelcat.ports.Platform` here and hands it to the app, which passes the ports
on to its parts.  Nothing else in the core imports ``pixelcat.adapters``.
"""
from __future__ import annotations

import sys

from .ports import Platform


def build_platform(platform: str = sys.platform) -> Platform:
    """The ports for ``platform`` (a ``sys.platform`` value): Windows, or the null adapter elsewhere."""
    if platform == "win32":
        from .adapters.windows import platform as windows

        return windows.build()
    from .adapters import null

    return null.build()
