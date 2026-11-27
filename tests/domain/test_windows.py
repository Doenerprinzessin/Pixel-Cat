"""pixelcat.domain.windows: AppWindow labels and find_windows (plain data, no platform calls)."""
from __future__ import annotations

import pytest

from pixelcat.domain import AppWindow, find_windows


def aw(title: str, exe: str = "app.exe", hwnd: int = 1, pid: int = 0, **kw) -> AppWindow:  # type: ignore[no-untyped-def]
    return AppWindow(hwnd, title, exe, kw.get("minimized", False), kw.get("maximized", False), kw.get("cls", ""), pid)


@pytest.mark.parametrize("win, label", [
    (aw("Inbox", "OUTLOOK.EXE"), "OUTLOOK: Inbox"),
    (aw("Song", "Spotify", minimized=True), "Spotify: Song (minimized)"),
    (aw("Doc", "winword.exe", maximized=True), "winword: Doc (maximized)"),
    (aw("x" * 61, "a.exe"), "a: " + "x" * 57 + "..."),
    (aw("x" * 60, "a.exe"), "a: " + "x" * 60),
])
def test_app_window_label(win, label):
    assert win.label() == label


WINDOWS = [
    aw("Inbox - Outlook", "OUTLOOK.EXE", 1),
    aw("Budget.xlsx - Excel", "EXCEL.EXE", 2),
    aw("Report.docx - Word", "WINWORD.EXE", 3),
    aw("Chat | Microsoft Teams", "ms-teams.exe", 4),
    aw("Old Teams", "Teams.exe", 5),
    aw("GitHub - Google Chrome", "chrome.exe", 6),
    aw("Untitled - Notepad", "notepad.exe", 7),
    aw("Notepad++ settings", "notepad++.exe", 8),
    aw("Pixel Cat project - Visual Studio Code", "Code.exe", 9),
]


@pytest.mark.parametrize("query, hwnds", [
    ("word", [3]), ("Excel", [2]), ("excel.exe", [2]), ("teams", [4, 5]), ("chrome", [6]),
    ("google chrome", [6]), ("vs code", [9]), ("notepad", [7]),
    ("pad", [7, 8]),                    # part of an exe name
    ("budget", [2]),                    # window title
    ("Inboxx - Outlook", [1]),          # fuzzy title
    ("", []), ("   ", []), ("zzz", []),
])
def test_find_windows(query, hwnds):
    assert [w.hwnd for w in find_windows(query, WINDOWS)] == hwnds
