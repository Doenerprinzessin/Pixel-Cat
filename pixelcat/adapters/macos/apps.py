"""AppsPort on macOS: the installed .app bundles, ``open`` for apps, files, folders and URLs, System Settings
pages (``x-apple.systempreferences:``), the editors and Ollama.

The app index reads each bundle's ``Info.plist`` (``CFBundleDisplayName`` / ``CFBundleName``) and, when pyobjc
is there, the name Finder shows in the user's language ("Rechner" for Calculator on a German Mac).  Matching
works like the Windows Start-menu search: exact name, then a name that starts with the words, then a whole
word, then a close spelling.
"""
from __future__ import annotations

import difflib
import logging
import os
import plistlib
import re
import shutil
import threading
import time
from pathlib import Path

from . import _objc

log = logging.getLogger(__name__)

OPEN = "/usr/bin/open"
NO_ADMIN = "macOS doesn't start apps as administrator"
RUN_AS_ADMIN = ("macOS doesn't start apps as administrator here - open it normally; macOS asks for a password "
                "itself if the app needs one.")
VSCODE_ID = "com.microsoft.VSCode"
OLLAMA_APP = Path("/Applications/Ollama.app")
BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")     # Homebrew on Apple Silicon and on Intel Macs


def app_roots() -> list[Path]:
    """Where apps are installed (each also one folder deep, e.g. /Applications/Microsoft Office/...)."""
    return [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
            Path("/System/Applications/Utilities"), Path.home() / "Applications"]


EXTRA_APPS = (Path("/System/Library/CoreServices/Finder.app"),)

# what people (and the Windows words the model knows) call an app -> the Mac app's name
_APP_NAME_ALIASES = {
    "file explorer": ["finder"], "explorer": ["finder"], "task manager": ["activity monitor"],
    "notepad": ["textedit"], "editor": ["textedit"], "settings": ["system settings"],
    "control panel": ["system settings"], "system preferences": ["system settings"],
    "snipping tool": ["screenshot"], "paint": ["preview"], "photos": ["photos", "preview"],
    "command prompt": ["terminal"], "cmd": ["terminal"], "powershell": ["terminal"],
    "store": ["app store"], "microsoft store": ["app store"], "media player": ["music", "quicktime player"],
    "movies": ["tv"], "sticky notes": ["stickies"], "alarms": ["clock"], "voice recorder": ["voice memos"],
    "camera": ["photo booth"], "edge": ["microsoft edge"], "word": ["microsoft word"],
    "excel": ["microsoft excel"], "powerpoint": ["microsoft powerpoint"], "outlook": ["microsoft outlook"],
    "teams": ["microsoft teams"], "vs code": ["visual studio code"], "vscode": ["visual studio code"],
    "chrome": ["google chrome"],
}
_SKIP = re.compile(r"uninstall|deinstall|readme|release notes|documentation|website", re.I)

# the chat's Settings pages (domain.apps.SETTINGS_PAGES) -> System Settings panes (bundle ids of the panes in
# /System/Library/ExtensionKit/Extensions); "" is System Settings itself
SETTINGS_IDS = {
    "home": "", "display": "com.apple.Displays-Settings.extension",
    "night light": "com.apple.Displays-Settings.extension", "sound": "com.apple.Sound-Settings.extension",
    "bluetooth": "com.apple.BluetoothSettings", "wifi": "com.apple.wifi-settings-extension",
    "network": "com.apple.Network-Settings.extension",
    "vpn": "com.apple.NetworkExtensionSettingsUI.NESettingsUIExtension",
    "notifications": "com.apple.Notifications-Settings.extension", "focus": "com.apple.Focus-Settings.extension",
    "power": "com.apple.Battery-Settings.extension", "battery": "com.apple.Battery-Settings.extension",
    "storage": "com.apple.settings.Storage", "apps": "com.apple.settings.Storage",
    "default apps": "com.apple.Desktop-Settings.extension", "startup apps": "com.apple.LoginItems-Settings.extension",
    "personalization": "com.apple.Appearance-Settings.extension",
    "background": "com.apple.Wallpaper-Settings.extension", "colors": "com.apple.Appearance-Settings.extension",
    "themes": "com.apple.Appearance-Settings.extension", "taskbar": "com.apple.Desktop-Settings.extension",
    "mouse": "com.apple.Mouse-Settings.extension", "keyboard": "com.apple.Keyboard-Settings.extension",
    "printers": "com.apple.Print-Scan-Settings.extension", "date and time": "com.apple.Date-Time-Settings.extension",
    "language": "com.apple.Localization-Settings.extension", "privacy": "com.apple.settings.PrivacySecurity.extension",
    "windows update": "com.apple.Software-Update-Settings.extension",
    "about": "com.apple.SystemProfiler.AboutExtension", "accessibility": "com.apple.Accessibility-Settings.extension",
}
TRACKPAD_ID = "com.apple.Trackpad-Settings.extension"     # "mouse" on a MacBook without a mouse

# ------------------------------------------------------------------ the app index
_index: tuple[float, dict[str, Path]] | None = None
_index_lock = threading.Lock()
_prefetching = False


def _names(app: Path) -> list[str]:
    """The names an app bundle goes by: its display name, its bundle name, its file name, the localized name
    (the last one is what Finder shows: the app's label)."""
    names: list[str] = []
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            info = plistlib.load(fh)
        for key in ("CFBundleDisplayName", "CFBundleName"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
    except Exception:  # noqa: BLE001 - a broken or unreadable bundle still has its file name
        pass
    names.append(app.stem)
    try:
        shown = _objc.FN.NSFileManager.defaultManager().displayNameAtPath_(str(app))
        if shown:
            names.append(str(shown).removesuffix(".app"))
    except Exception:  # noqa: BLE001 - pyobjc missing: the plist names are enough
        pass
    return names


def _bundles() -> list[Path]:
    found: list[Path] = []
    for root in app_roots():
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".app":
                found.append(entry)
            elif entry.is_dir() and not entry.name.startswith("."):
                try:
                    found += sorted(p for p in entry.iterdir() if p.suffix == ".app")
                except OSError:
                    continue
    found += [p for p in EXTRA_APPS if p.is_dir()]
    return found


def build_index() -> dict[str, Path]:
    """Installed apps, name -> the .app bundle (the first one found wins)."""
    global _labels
    apps: dict[str, Path] = {}
    labels: dict[Path, str] = {}
    for app in _bundles():
        names = _names(app)
        labels.setdefault(app, names[-1])
        for name in names:
            if not _SKIP.search(name):
                apps.setdefault(name, app)
    _labels = labels
    return apps


_labels: dict[Path, str] = {}    # bundle -> the name Finder shows ("Visual Studio Code", not "Code")


def installed_apps(max_age: float = 120.0) -> dict[str, Path]:
    global _index
    with _index_lock:
        if _index is None or time.monotonic() - _index[0] > max_age:
            _index = (time.monotonic(), build_index())
        return _index[1]


def label(app: Path, fallback: str) -> str:
    return _labels.get(app) or fallback


def _prefetch_job() -> None:
    global _prefetching
    try:
        installed_apps()
    except Exception:  # noqa: BLE001
        log.info("indexing the apps failed", exc_info=True)
    finally:
        _prefetching = False


def best_match(name: str, apps: dict[str, Path]) -> tuple[str, Path] | None:
    """The Windows Start-menu matching: exact, starts with, whole word, close spelling."""
    q = name.strip().lower()
    if not q:
        return None
    low = {k.lower(): k for k in apps}
    if q in low:
        return low[q], apps[low[q]]
    starts = sorted((k for lk, k in low.items() if lk.startswith(q)), key=len)
    if starts:
        return starts[0], apps[starts[0]]
    words = sorted((k for lk, k in low.items() if re.search(rf"\b{re.escape(q)}\b", lk)), key=len)
    if words:
        return words[0], apps[words[0]]
    close = difflib.get_close_matches(q, list(low), n=1, cutoff=0.72)
    if close:
        return low[close[0]], apps[low[close[0]]]
    return None


def _has_battery() -> bool:
    try:
        return "InternalBattery" in (_objc.run(["/usr/bin/pmset", "-g", "batt"], timeout=5).stdout or "")
    except Exception:  # noqa: BLE001
        return False


def _open(target: str) -> None:
    if target.startswith("-"):
        target = os.path.abspath(target)      # never read as one of open's options
    try:
        res = _objc.run([OPEN, target], timeout=30)
    except OSError as exc:
        raise OSError(f"macOS couldn't open {target}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - subprocess.TimeoutExpired
        raise OSError(f"macOS didn't open {target} in time") from exc
    if res.returncode != 0:
        why = (res.stderr or "").strip().splitlines()
        raise OSError(f"macOS couldn't open {target}" + (f": {why[-1]}" if why else "") +
                      " - check that it exists and an app is installed that opens it")


class MacApps:
    def prefetch(self) -> None:
        """Index the installed apps in the background (find_shortcut then answers at once)."""
        global _prefetching
        if _index is None and not _prefetching:
            _prefetching = True
            threading.Thread(target=_prefetch_job, name="mac-apps", daemon=True).start()

    def find_shortcut(self, name: str) -> tuple[str, Path | str] | None:
        """The installed app ``name`` means: (its name as Finder shows it, the .app bundle)."""
        apps = installed_apps()
        low = {k.lower(): v for k, v in apps.items()}
        for alias in _APP_NAME_ALIASES.get(name.strip().lower(), []):    # the Mac's own app for a Windows word
            if alias in low:
                return label(low[alias], alias), low[alias]
        hit = best_match(name, apps)
        return (label(hit[1], hit[0]), hit[1]) if hit is not None else None

    def open(self, target: str | Path) -> None:
        _open(str(target))

    def open_elevated(self, file: str, args: list[str]) -> None:
        raise OSError(NO_ADMIN)

    def open_settings(self, page: str) -> None:
        pane = SETTINGS_IDS.get(page)
        if pane is None:
            raise OSError(f"System Settings has no page for '{page}'")
        if page == "mouse" and _has_battery():
            pane = TRACKPAD_ID
        _open("x-apple.systempreferences:" + pane)

    def needs_admin(self, target: object) -> bool:
        return False

    def editor_command(self, app: str) -> list[str] | None:
        """``open -a <VS Code>`` or ``open -e`` (TextEdit); the file goes last."""
        if app == "notepad":
            return [OPEN, "-e"]
        if app != "vscode":
            return None
        try:
            url = _objc.AK.NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(VSCODE_ID)
            path = url.path() if url is not None else None
        except Exception:  # noqa: BLE001 - pyobjc missing
            log.debug("looking for VS Code failed", exc_info=True)
            path = None
        return [OPEN, "-a", str(path)] if path else None

    def find_ollama(self) -> Path | None:
        """The Ollama app (it runs the server), else the ``ollama`` program (Homebrew)."""
        for app in (OLLAMA_APP, Path.home() / "Applications" / "Ollama.app"):
            if app.is_dir():
                return app
        found = shutil.which("ollama")      # (a login item's PATH is short: look in Homebrew's folders too)
        if found:
            return Path(found)
        for folder in BIN_DIRS:
            exe = Path(folder) / "ollama"
            if exe.is_file() and os.access(exe, os.X_OK):
                return exe
        return None

    def start_ollama(self, exe: Path) -> None:
        """The app in the background (``open -g -a``), else ``ollama serve`` detached; gives up quietly."""
        try:
            if exe.suffix == ".app":
                _objc.run([OPEN, "-g", "-a", str(exe)], timeout=30)
            else:
                _objc.spawn([str(exe), "serve"], cwd=str(exe.parent))
        except Exception:  # noqa: BLE001
            log.warning("couldn't start Ollama (%s)", exe, exc_info=True)

    def run_as_admin(self, target: str | Path) -> tuple[bool, str]:
        return (False, RUN_AS_ADMIN)
