"""The application: ``PixelCatApp`` (in ``controller.py``) and the parts it is made of.

``PixelCatApp`` is loaded on first use, so the plain-Python modules in here (``context``, ``geometry``) can be
imported without Qt."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .controller import PixelCatApp

__all__ = ["PixelCatApp"]


def __getattr__(name: str) -> Any:
    if name == "PixelCatApp":
        from .controller import PixelCatApp
        return PixelCatApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
