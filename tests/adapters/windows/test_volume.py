"""pixelcat.adapters.windows.volume: master volume via raw COM vtables (fake in-memory COM objects)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from adapters.windows.fakes import FakeCom, FakeDLL, write_ptr
from pixelcat.adapters.windows import volume

P = ctypes.c_void_p


@pytest.fixture
def audio(monkeypatch):  # type: ignore[no-untyped-def]
    """Enumerator -> default device -> IAudioEndpointVolume, all fake."""
    st = SimpleNamespace(level=0.5, muted=False, create_hr=0, endpoint_hr=0, activate_hr=0)

    def get_level(p):  # type: ignore[no-untyped-def]
        ctypes.c_float.from_address(p).value = st.level

    def get_mute(p):  # type: ignore[no-untyped-def]
        wintypes.BOOL.from_address(p).value = st.muted

    def set_level(value, ctx):  # type: ignore[no-untyped-def]
        st.level = value

    def set_mute(value, ctx):  # type: ignore[no-untyped-def]
        st.muted = bool(value)

    st.vol = FakeCom({9: ((P,), get_level), 15: ((P,), get_mute), 7: ((ctypes.c_float, P), set_level),
                      14: ((wintypes.BOOL, P), set_mute)})

    def activate(iid, ctx, params, pp):  # type: ignore[no-untyped-def]
        if st.activate_hr:
            return st.activate_hr
        assert bytes(ctypes.string_at(iid, 4)) == bytes(ctypes.string_at(ctypes.addressof(volume.IID_IAudioEndpointVolume), 4))
        assert ctx == volume.CLSCTX_ALL
        write_ptr(pp, st.vol.ptr)

    st.device = FakeCom({3: ((P, wintypes.DWORD, P, P), activate)})

    def default_endpoint(flow, role, pp):  # type: ignore[no-untyped-def]
        if st.endpoint_hr:
            return st.endpoint_hr
        assert (flow, role) == (volume.E_RENDER, volume.E_CONSOLE)
        write_ptr(pp, st.device.ptr)

    st.enum = FakeCom({4: ((ctypes.c_int, ctypes.c_int, P), default_endpoint)})
    ole = FakeDLL("ole32")

    def create(clsid, outer, ctx, iid, out):  # type: ignore[no-untyped-def]
        if st.create_hr:
            return st.create_hr
        out._obj.value = st.enum.ptr
        return 0

    ole.CoCreateInstance.result = create
    monkeypatch.setattr(volume, "_ole32", ole)
    st.ole = ole
    return st


def released(st) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    return st.enum.released, st.device.released, st.vol.released


@pytest.mark.parametrize("level, muted, expected", [(0.437, True, (44, True)), (1.0, False, (100, False)),
                                                    (0.0, False, (0, False))])
def test_get_volume(audio, level, muted, expected):
    audio.level, audio.muted = level, muted
    assert volume.get_volume() == expected
    assert released(audio) == (1, 1, 1)
    assert audio.ole.CoInitializeEx.calls == [(None, volume.COINIT_APARTMENTTHREADED)]


@pytest.mark.parametrize("percent, stored, unmuted", [(42, 42, True), (150, 100, True), (-5, 0, False),
                                                      ("37", 37, True), (0, 0, False)])
def test_set_volume_clamps_and_unmutes(audio, percent, stored, unmuted):
    audio.muted = True
    assert volume.set_volume(percent) == stored
    assert audio.level == pytest.approx(stored / 100)
    assert audio.muted is (not unmuted)
    assert released(audio) == (1, 1, 1)


@pytest.mark.parametrize("muted", [True, False])
def test_set_mute(audio, muted):
    audio.muted = not muted
    volume.set_mute(muted)
    assert audio.muted is muted and released(audio) == (1, 1, 1)


def test_no_audio_service(audio):
    audio.create_hr = -2147221164
    with pytest.raises(OSError, match="CoCreateInstance failed 0x80040154"):
        volume.get_volume()


def test_no_default_device_releases_the_enumerator(audio):
    audio.endpoint_hr = -2147023728          # E_NOTFOUND
    with pytest.raises(OSError, match="no default audio device"):
        volume.set_volume(10)
    assert released(audio) == (1, 0, 0)


def test_activate_failure_releases_device_and_enumerator(audio):
    audio.activate_hr = -2147024809
    with pytest.raises(OSError, match="could not activate endpoint volume"):
        volume.set_mute(True)
    assert released(audio) == (1, 1, 0)


@pytest.mark.windows
def test_guid_from_str_is_the_windows_binary_layout():
    g = volume.GUID.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    assert (g.Data1, g.Data2, g.Data3, bytes(g.Data4)) == (0xBCDE0395, 0xE52F, 0x467C, bytes.fromhex("8E3DC4579291692E"))
