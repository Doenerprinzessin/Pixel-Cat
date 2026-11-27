"""Windows "known folders" - asked from Windows itself, because companies often redirect them
(Desktop/Documents to a network share, Pictures to OneDrive "Bilder" ...)."""
from __future__ import annotations

from pathlib import Path

_KNOWN_FOLDER_IDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
_known_cache: dict[str, Path | None] = {}


def shell_folder(name: str) -> Path | None:
    """The real location of a Windows known folder (SHGetKnownFolderPath), or None."""
    if name in _known_cache:
        return _known_cache[name]
    path: Path | None = None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(_KNOWN_FOLDER_IDS[name]).bytes_le)
        out = ctypes.c_wchar_p()
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        fn.restype = ctypes.c_long
        if fn(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0 and out.value:
            path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:  # noqa: BLE001 - fall back to the usual place
        path = None
    _known_cache[name] = path
    return path
