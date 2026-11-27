"""pixelcat.desktop.uia: reading other apps through UI Automation.

The COM calls go through ``uia._call``; the tests replace it with a dispatcher over fake
Python objects (``FakeUia``), so the Client logic (filtering, ordering, ids, text
fallbacks, releasing) runs on any OS.  ``_call`` itself is tested on a real in-memory
vtable (``FakeCom``).
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field

import pytest

from desktop.fakes import FakeCom, FakeDLL, raising, write_ptr
from pixelcat.desktop import uia
from pixelcat.desktop.uia import Element, Snapshot

BUTTON, EDIT, TEXT, LISTITEM, DOCUMENT, CHECKBOX, PANE = 50000, 50004, 50020, 50007, 50030, 50002, 50033


@dataclass
class El:
    name: str = ""
    ctype: int = BUTTON
    rect: tuple = (10, 10, 50, 30)
    value: str | None = None
    toggle: int | None = None
    enabled: bool = True
    focus: bool = False
    password: bool = False
    fail: bool = False
    text_pattern: str | None = None
    pattern_error: bool = False


@dataclass
class FakeUia:
    """The COM side: objects by pointer, BSTRs in real memory."""
    window: tuple = (0, 0, 800, 600)
    elements: list = field(default_factory=list)
    focused: El | None = None
    document: El | None = None
    conditions: list = field(default_factory=list)
    cached: list = field(default_factory=list)
    released: list = field(default_factory=list)
    objects: dict = field(default_factory=dict)
    bstrs: dict = field(default_factory=dict)

    AUTOMATION, ROOT, CACHE, ARRAY = 0xA000, 0xA001, 0xA002, 0xA003

    def new(self, obj) -> int:  # type: ignore[no-untyped-def]
        ptr = 0xB000 + len(self.objects)
        self.objects[ptr] = obj
        return ptr

    def bstr(self, text: str) -> int:
        buf = ctypes.create_unicode_buffer(text)
        addr = ctypes.addressof(buf)
        self.bstrs[addr] = (buf, len(text))
        return addr

    def variant(self, v: uia.VARIANT, value) -> None:  # type: ignore[no-untyped-def]
        if isinstance(value, bool):
            v.vt, v.u.boolVal = uia.VT_BOOL, -1 if value else 0
        elif isinstance(value, int):
            v.vt, v.u.lVal = uia.VT_I4, value
        elif isinstance(value, str):
            v.vt, v.u.ptr = uia.VT_BSTR, self.bstr(value)
        else:
            v.vt = uia.VT_EMPTY

    def call(self, ptr: int, index: int, argtypes: tuple, *args) -> int:  # type: ignore[no-untyped-def]
        if ptr == self.AUTOMATION:
            return self.automation(index, args)
        if ptr == self.CACHE:
            assert index == uia._AddProperty
            self.cached.append(args[0])
            return 0
        if ptr == self.ARRAY:
            if index == uia._ArrLength:
                args[0]._obj.value = len(self.elements)
            else:
                el = self.elements[args[0]]
                args[1]._obj.value = 0 if el is None else self.new(el)
            return 0
        if ptr == self.ROOT:
            return self.root(index, args)
        return self.element(self.objects[ptr], index, args)

    def automation(self, index: int, args: tuple) -> int:
        out = args[-1]._obj
        if index == uia._ElementFromHandle:
            out.value = self.ROOT
        elif index == uia._GetFocusedElement:
            out.value = self.new(self.focused) if self.focused else 0
        elif index == uia._CreateCacheRequest:
            out.value = self.CACHE
        elif index == uia._CreatePropertyCondition:
            prop, v = args[0], args[1]
            val = bool(v.u.boolVal) if v.vt == uia.VT_BOOL else v.u.lVal
            self.conditions.append(("prop", prop, val))
            out.value = self.new(("cond", len(self.conditions)))
        elif index in (uia._CreateAndCondition, uia._CreateOrCondition):
            self.conditions.append(("and" if index == uia._CreateAndCondition else "or",))
            out.value = self.new(("cond", len(self.conditions)))
        else:
            raise AssertionError(index)
        return 0

    def root(self, index: int, args: tuple) -> int:
        if index == uia._CurBounds:
            r = args[0]._obj
            r.left, r.top, r.right, r.bottom = self.window
        elif index == uia._FindAllBuildCache:
            assert args[0] == uia.TreeScope_Descendants
            args[-1]._obj.value = self.ARRAY
        elif index == uia._FindFirst:
            assert args[0] == uia.TreeScope_Subtree
            args[-1]._obj.value = self.new(self.document) if self.document else 0
        else:
            raise AssertionError(index)
        return 0

    def element(self, el, index: int, args: tuple) -> int:  # type: ignore[no-untyped-def]
        if isinstance(el, tuple) and el[0] == "range":
            assert index == uia._RangeGetText
            self.range_max = args[0]
            args[1]._obj.value = self.bstr(el[1][: args[0]])
            return 0
        if isinstance(el, tuple) and el[0] == "pattern":
            assert index == uia._DocumentRange
            args[0]._obj.value = self.new(("range", el[1]))
            return 0
        if el.fail:
            raise OSError("element went away")
        if index in (uia._CachedBounds, uia._CurBounds):
            r = args[0]._obj
            r.left, r.top, r.right, r.bottom = el.rect
        elif index in (uia._CachedControlType, uia._CurControlType):
            args[0]._obj.value = el.ctype
        elif index in (uia._CachedIsPassword, uia._CurIsPassword):
            args[0]._obj.value = int(el.password)
        elif index == uia._CachedEnabled:
            args[0]._obj.value = int(el.enabled)
        elif index == uia._CachedHasFocus:
            args[0]._obj.value = int(el.focus)
        elif index in (uia._CachedName, uia._CurName):
            args[0]._obj.value = self.bstr(el.name) if el.name else 0
        elif index in (uia._GetCachedPropertyValue, uia._GetCurrentPropertyValue):
            prop, v = args[0], args[1]._obj
            self.variant(v, {uia.P_VALUE: el.value, uia.P_TOGGLESTATE: el.toggle}[prop])
        elif index == uia._GetCurrentPatternAs:
            if el.pattern_error:
                raise OSError("no pattern")
            assert args[0] == uia.PATTERN_TEXT
            args[-1]._obj.value = self.new(("pattern", el.text_pattern)) if el.text_pattern is not None else 0
        else:
            raise AssertionError(index)
        return 0


@pytest.fixture
def com(monkeypatch):  # type: ignore[no-untyped-def]
    fake = FakeUia()
    ole32, oleaut32 = FakeDLL("ole32"), FakeDLL("oleaut32")

    def create(clsid, outer, ctx, iid, out):  # type: ignore[no-untyped-def]
        out._obj.value = fake.AUTOMATION
        return 0

    ole32.CoCreateInstance.result = create
    oleaut32.SysStringLen.result = lambda p: fake.bstrs[p][1]
    monkeypatch.setattr(uia, "ole32", ole32)
    monkeypatch.setattr(uia, "oleaut32", oleaut32)
    monkeypatch.setattr(uia, "_call", fake.call)
    monkeypatch.setattr(uia, "_release", lambda p: fake.released.append(p) if p else None)
    fake.ole32, fake.oleaut32 = ole32, oleaut32
    return fake


# ---------------------------------------------------------------- Client lifetime
@pytest.mark.parametrize("init_hr, uninit", [(0, 1), (1, 1), (-2147417850, 0)])
def test_client_not_available(com, init_hr, uninit):
    com.ole32.CoInitializeEx.result = init_hr
    com.ole32.CoCreateInstance.result = -2147221164
    with pytest.raises(OSError, match=r"UI Automation is not available \(0x80040154\)"):
        uia.Client()
    assert len(com.ole32.CoUninitialize.calls) == uninit


def test_client_close_releases_everything_once(com):
    c = uia.Client()
    assert com.ole32.CoInitializeEx.calls == [(None, uia.COINIT_MULTITHREADED)]
    root = c.element_from_handle(42)
    cache = c._keep(0xC1)
    c._keep(0)
    c.close()
    assert com.released == [cache, root, com.AUTOMATION] and c.ptr == 0
    c.close()
    assert len(com.ole32.CoUninitialize.calls) == 1


def test_client_in_an_sta_thread_does_not_uninitialize(com):
    com.ole32.CoInitializeEx.result = -2147417850     # RPC_E_CHANGED_MODE
    with uia.Client():
        pass
    assert com.ole32.CoUninitialize.calls == []


# ---------------------------------------------------------------- snapshot
def test_snapshot_reads_filters_and_numbers_elements_in_reading_order(com):
    com.window = (0, 0, 800, 600)
    com.elements = [
        El("Save", BUTTON, (300, 100, 360, 124)),
        El("File", BUTTON, (10, 104, 60, 124)),                     # same row (y // 12) -> left of Save
        El("Name", EDIT, (10, 200, 300, 224), value="Felix", focus=True),
        El("PIN", EDIT, (10, 240, 300, 264), value="1234", password=True),
        El("Agree", CHECKBOX, (10, 300, 30, 320), toggle=1),
        El("Spam", CHECKBOX, (10, 330, 30, 350), toggle=0, enabled=False),
        El("", TEXT, (10, 400, 300, 420)),                          # nameless text: dropped
        El("", LISTITEM, (10, 430, 300, 450), value="row value"),   # has a value: kept
        El("Tiny", BUTTON, (10, 500, 11, 520)),                     # < 2 px wide
        El("Outside", BUTTON, (800, 10, 900, 30)),                  # right of the window
        El("Gone", BUTTON, fail=True),
        None,                                                       # GetElement gave nothing
        El("Odd", 49999, (10, 560, 60, 580)),
    ]
    snap = uia.snapshot(1234)
    assert isinstance(snap, Snapshot) and snap.hwnd == 1234 and snap.window_rect == (0, 0, 800, 600)
    got = [(e.id, e.kind, e.name, e.value, e.enabled, e.focused, e.password, e.checked) for e in snap.elements]
    assert got == [
        (1, "button", "File", "", True, False, False, None),
        (2, "button", "Save", "", True, False, False, None),
        (3, "edit", "Name", "Felix", True, True, False, None),
        (4, "edit", "PIN", "", True, False, True, None),
        (5, "checkbox", "Agree", "", True, False, False, True),
        (6, "checkbox", "Spam", "", False, False, False, False),
        (7, "list item", "", "row value", True, False, False, None),
        (8, "element", "Odd", "", True, False, False, None),
    ]
    assert snap.focused is snap.elements[2]
    assert snap.took_ms >= 0
    assert len([p for p in com.released if p >= 0xB000 and not isinstance(com.objects[p], tuple)]) == 12
    assert len(com.ole32.CoUninitialize.calls) == 1


def test_snapshot_conditions_and_cached_properties(com):
    uia.snapshot(1, kinds=(BUTTON, EDIT, TEXT))
    assert com.conditions == [("prop", uia.P_OFFSCREEN, False), ("prop", uia.P_CONTROLTYPE, BUTTON),
                              ("prop", uia.P_CONTROLTYPE, EDIT), ("prop", uia.P_CONTROLTYPE, TEXT),
                              ("or",), ("or",), ("and",)]
    assert com.cached == [uia.P_NAME, uia.P_CONTROLTYPE, uia.P_BOUNDS, uia.P_ENABLED, uia.P_HASFOCUS,
                          uia.P_ISPASSWORD, uia.P_VALUE, uia.P_TOGGLESTATE]


def test_snapshot_without_values(com):
    com.elements = [El("Box", CHECKBOX, toggle=1, value="v")]
    with uia.Client() as c:
        (el,) = c.snapshot(1, with_values=False).elements
    assert el.value == "" and el.checked is None
    assert uia.P_VALUE not in com.cached


def test_snapshot_limit_and_empty(com):
    com.elements = [El(f"B{i}", BUTTON, (10, 10 + 20 * i, 60, 28 + 20 * i)) for i in range(10)]
    assert [e.id for e in uia.snapshot(1, limit=3).elements] == [1, 2, 3]
    com.elements = []
    snap = uia.snapshot(1)
    assert snap.elements == [] and snap.focused is None


# ---------------------------------------------------------------- focus and text
def test_focused_element(com):
    com.focused = El("Search", EDIT, (5, 6, 105, 26), value="cats")
    assert uia.focused_element() == Element(0, "edit", "Search", (5, 6, 105, 26), "cats", True, True, False)
    com.focused = El("Password", EDIT, value="secret", password=True)
    assert uia.focused_element().value == ""
    com.focused = None
    assert uia.focused_element() is None


@pytest.mark.parametrize("focused, expected", [
    (El("Doc", DOCUMENT, text_pattern="Hello world"), "Hello world"),
    (El("Field", EDIT, value="typed", pattern_error=True), "typed"),
    (El("Combo", 50003, value="choice"), "choice"),
    (El("Field", EDIT, value=None), ""),
    (El("Button", BUTTON, value="x"), None),
    (El("PIN", EDIT, value="1234", password=True, text_pattern="1234"), None),
    (None, None),
])
def test_focused_text(com, focused, expected):
    com.focused = focused
    assert uia.focused_text() == expected


def test_focused_text_passes_the_limit(com):
    com.focused = El("Doc", DOCUMENT, text_pattern="abcdefghij")
    assert uia.focused_text(max_chars=4) == "abcd" and com.range_max == 4


@pytest.mark.parametrize("document, expected", [
    (El("Editor", DOCUMENT, text_pattern="Dear Sir"), "Dear Sir"),
    (El("Editor", DOCUMENT, text_pattern=None), ""),
    (El("Secret", EDIT, password=True, text_pattern="x"), ""),
    (None, ""),
])
def test_document_text(com, document, expected):
    com.document = document
    assert uia.document_text(5, max_chars=100) == expected


def test_text_condition_asks_for_the_text_pattern(com):
    uia.document_text(5)
    assert com.conditions == [("prop", uia.P_TEXTPATTERN_AVAILABLE, True)]


def test_module_functions_swallow_com_errors(com):
    com.ole32.CoCreateInstance.result = -1
    assert uia.focused_element() is None
    assert uia.focused_text() is None
    assert uia.document_text(1) == ""
    with pytest.raises(OSError):
        uia.snapshot(1)


def test_bstrs_are_freed(com):
    com.focused = El("Search", EDIT, value="cats")
    uia.focused_element()
    assert len(com.oleaut32.SysFreeString.calls) == 1        # the name; VARIANTs are cleared instead
    assert len(com.oleaut32.VariantClear.calls) == 1


# ---------------------------------------------------------------- Element / GUID
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


def test_guid_of():
    g = uia.GUID.of("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    assert (g.d1, g.d2, g.d3, bytes(g.d4)) == (0xFF48DBA4, 0x60EF, 0x4201, bytes.fromhex("aa8754103eef594e"))


# ---------------------------------------------------------------- the raw vtable calls
def test_call_out_ptr_get_int_and_release_use_the_vtable(monkeypatch):
    monkeypatch.setattr(uia, "_PROTOS", {})
    inner = FakeCom()

    def get_obj(pp):  # type: ignore[no-untyped-def]
        write_ptr(pp, inner.ptr)

    def get_int(p):  # type: ignore[no-untyped-def]
        ctypes.c_int.from_address(p).value = 1234

    def add(prop):  # type: ignore[no-untyped-def]
        return prop - 30000

    com = FakeCom({uia._GetFocusedElement: ((ctypes.c_void_p,), get_obj),
                   uia._CurControlType: ((ctypes.c_void_p,), get_int),
                   uia._AddProperty: ((ctypes.c_int,), add)})
    assert uia._out_ptr(com.ptr, uia._GetFocusedElement) == inner.ptr
    assert uia._get_int(com.ptr, uia._CurControlType) == 1234
    assert uia._call(com.ptr, uia._AddProperty, (ctypes.c_int,), uia.P_NAME) == 5
    uia._release(inner.ptr)
    uia._release(0)
    assert inner.released == 1 and com.released == 0


@pytest.mark.windows
def test_call_turns_failed_hresults_into_oserror():
    com = FakeCom({uia._AddProperty: ((ctypes.c_int,), lambda prop: -2147024809)})   # E_INVALIDARG
    with pytest.raises(OSError):
        uia._call(com.ptr, uia._AddProperty, (ctypes.c_int,), 1)


def test_variant_str_reads_bstr_int_bool_and_clears(com):
    fake = com
    for value, text in (("abc", "abc"), (7, "7"), (True, "true"), (False, "false"), (None, "")):
        el = El(value=value)
        ptr = fake.new(el)
        assert uia._variant_str(ptr, uia._GetCurrentPropertyValue, uia.P_VALUE) == text
    assert len(fake.oleaut32.VariantClear.calls) == 5
    assert uia._bstr(0) == ""


def test_snapshot_of_failing_root_raises(com, monkeypatch):
    monkeypatch.setattr(com, "root", raising(OSError("window closed")))
    with pytest.raises(OSError):
        uia.snapshot(1)
    assert com.AUTOMATION in com.released
