"""The app's plain-Python helpers: the chat context sent to the model, and window geometry (no Qt needed)."""
from __future__ import annotations

from pixelcat.app import context
from pixelcat.app.geometry import rect_gap


def test_clean_drops_private_keys_and_shortens() -> None:
    m = {"role": "user", "content": "x" * 50, "_tool": True, "images": ["b64"]}
    assert context.clean(m) == {"role": "user", "content": "x" * 50, "images": ["b64"]}
    assert context.clean(m, 10, keep_images=False) == {"role": "user", "content": "x" * 10 + " ...(shortened)"}


def test_token_estimate() -> None:
    assert context.tokens([{"content": "x" * 30}]) == 10 + 8 + len('""') // 3
    assert context.tokens([{"content": "", "images": ["a", "b"]}]) == 8 + 3200


def test_scrub_and_rect_gap() -> None:
    assert context.scrub("Done (Steps I took: a, b) [memory note: x] ok") == "Done ok"
    assert rect_gap((0, 0, 10, 10), (5, 5, 20, 20)) == 0
    assert rect_gap((0, 0, 10, 10), (13, 14, 20, 20)) == 5.0
