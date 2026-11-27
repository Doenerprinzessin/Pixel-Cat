"""pixelcat.domain.accessibility: how an Element is described to the AI (plain data)."""
from __future__ import annotations

import pytest

from pixelcat.domain import Element


@pytest.mark.parametrize("el, origin, scale, text", [
    (Element(1, "button", "OK", (100, 100, 140, 120)), (0, 0), 1.0, "[1] button 'OK' at 120,110"),
    (Element(2, "edit", "Name", (100, 100, 200, 120), value="Felix", focused=True), (100, 100), 0.5,
     "[2] edit 'Name' value='Felix' (focused) at 25,5"),
    (Element(3, "edit", "PIN", (0, 0, 10, 10), value="1234", password=True), (0, 0), 1.0,
     "[3] edit 'PIN' (password field) at 5,5"),
    (Element(4, "checkbox", "Agree", (0, 0, 10, 10), checked=False, enabled=False), (0, 0), 1.0,
     "[4] checkbox 'Agree' (unchecked) (disabled) at 5,5"),
    (Element(5, "checkbox", "", (0, 0, 10, 10), checked=True), (0, 0), 1.0, "[5] checkbox (checked) at 5,5"),
    (Element(6, "document", "Doc", (0, 0, 10, 10), value="long text"), (0, 0), 1.0, "[6] document 'Doc' at 5,5"),
    (Element(7, "text", "a\nb  ", (0, 0, 10, 10)), (0, 0), 1.0, "[7] text 'a b' at 5,5"),
    (Element(8, "link", "x" * 61, (0, 0, 10, 10)), (0, 0), 1.0, "[8] link '" + "x" * 57 + "...' at 5,5"),
    (Element(9, "edit", "", (0, 0, 10, 10), value="v" * 51 + "\n"), (0, 0), 1.0,
     "[9] edit value='" + "v" * 50 + "...' at 5,5"),
])
def test_element_describe(el, origin, scale, text):
    assert el.describe(origin, scale) == text


def test_element_center():
    assert Element(0, "x", "", (-10, 0, 11, 5)).center == (0, 2)
