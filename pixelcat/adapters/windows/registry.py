"""The protected-save marks: HKCU values named after the data folders that already use the protected save.

Each function raises OSError like winreg (persistence decides what to do about it).
"""
from __future__ import annotations

import os

# HKCU: folders already converted (the installer tests keep theirs apart, as their other keys)
REG_PATH = (r"Software\PixelCatInstallTest\ProtectedSaves" if os.environ.get("PIXELCAT_TEST_ROOT")
            else r"Software\PixelCat\ProtectedSaves")


def is_marked(folder_id: str) -> bool:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
        return bool(winreg.QueryValueEx(k, folder_id)[0])


def mark(folder_id: str) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, folder_id, 0, winreg.REG_DWORD, 1)


def forget(folder_id: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, folder_id)
