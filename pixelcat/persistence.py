"""The cat's saved state, protected so the Cat Pass can't be changed by editing a file.

Files in %APPDATA%/PixelCat:
* ``save.dat`` - the state, encrypted by Windows for this account (see savecrypt).  An edited
  file, or one copied from another account/PC, can't be read.
* ``save.bak`` - the same, written right after it: if ``save.dat`` is damaged or was changed,
  the cat loads this one instead (nothing is lost).
* ``state.json`` - the old plain-text save.  Converted once, then deleted; a folder that has
  been converted remembers it (registry, current user), so a new state.json is ignored.
Every load also checks that the XP adds up: a game gives at most MAX_XP_PER_PLAY, so the XP
can't be more than the games played could give.

Export/import (``.pixelcat`` files): the progress (Cat Pass XP, cosmetics, records, look) in a
signed file that loads on any PC - the way to move it to a new PC or Windows account.  Chat
permissions and auto mode are never part of it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths, savecrypt

log = logging.getLogger(__name__)

SAVE_NAME, BACKUP_NAME, LEGACY_NAME = "save.dat", "save.bak", "state.json"
_MAGIC_WIN = b"PXCAT1W\n"                # save.dat: Windows data protection
_MAGIC_SIGNED = b"PXCAT1S\n"             # save.dat fallback (Windows couldn't protect it): signed
EXPORT_EXT = ".pixelcat"
_EXPORT_TAG = "PXCAT-EXPORT-1:"
MAX_XP_PER_PLAY = 200                    # every game gives less (the most today: ~100)
# HKCU: folders already converted (the installer tests keep theirs apart, as their other keys)
REG_PATH = (r"Software\PixelCatInstallTest\ProtectedSaves" if os.environ.get("PIXELCAT_TEST_ROOT")
            else r"Software\PixelCat\ProtectedSaves")

# Progress that an export carries.  Not: chat permissions, auto mode, watches, window
# position, first-run questions - an imported file must never switch those on.
PROGRESS_FIELDS = ("xp", "skin", "hat", "best_round", "game_best", "game_plays", "tutorials_seen", "fish_book",
                   "pets", "feeds", "plays", "ui_style", "ui_accent", "ui_size", "ui_font")

last_load = "fresh"                       # how the last load_state went (the app tells the user if needed)


class SaveError(Exception):
    """A file that can't be imported - the message is for the user."""


@dataclass
class SavedState:
    version: int = 1
    energy: float = 0.8
    hunger: float = 0.2
    mood: float = 0.7
    skin: str = "charcoal"                   # Cosmetics -> Skins (unlocked through the Cat Pass)
    hat: str = ""                            # Cosmetics -> Hats ("" = no hat)
    xp: int = 0                              # Cat Pass XP from minigames (the stage follows from it)
    quiet: bool = False
    autostart_asked: bool = False
    last_seen: float = field(default_factory=time.time)
    pos_x: int | None = None
    pos_y: int | None = None
    pets: int = 0
    feeds: int = 0
    plays: int = 0                           # ball games played
    best_round: int = 0                      # most rounds in one ball game
    game_best: dict[str, float] = field(default_factory=dict)    # best score per minigame
    game_plays: dict[str, int] = field(default_factory=dict)     # how often each minigame was played
    tutorials_seen: list[str] = field(default_factory=list)      # games whose explanation was shown
    fish_book: dict[str, Any] = field(default_factory=dict)      # Fishing: every kind of fish caught
    model_download_ok: bool = False          # the user agreed to download the AI model (installer, /getmodel)
    ui_style: str = "catchat"                # Settings -> Style / Accent color / Size / Font
    ui_accent: str = "default"
    ui_size: str = "normal"
    ui_font: str = "default"
    auto_approved_tools: list[str] = field(default_factory=list)  # chat tools the user said ALWAYS to
    auto_mode: bool = False                  # chat actions run without confirmation cards
    watches: list[dict[str, Any]] = field(default_factory=list)   # wait_for watches (long tasks) to resume
    ext_settings: dict[str, Any] = field(default_factory=dict)    # external AI models: sign-in kind, limits, models
    ext_usage: dict[str, Any] = field(default_factory=dict)       # external AI models: tokens used this period


# ------------------------------------------------------------------ checking what was read
def _int(v: Any, default: int = 0) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError, OverflowError):
        return default


def xp_limit(st: SavedState) -> int:
    """The most XP the games played so far could have given."""
    return MAX_XP_PER_PLAY * (sum(st.game_plays.values()) + st.plays)


def _from_dict(data: Any, only: tuple[str, ...] | None = None) -> SavedState:
    """A SavedState from loaded data: unknown keys ignored, wrong types back to defaults, and
    the XP no more than the games played could give."""
    st = SavedState()
    if not isinstance(data, dict):
        raise savecrypt.Unreadable("not a save")
    fresh = SavedState()
    for key, value in data.items():
        if hasattr(st, key) and (only is None or key in only):
            setattr(st, key, value)
    for name in ("energy", "hunger", "mood"):
        try:
            setattr(st, name, min(1.0, max(0.0, float(getattr(st, name)))))
        except (TypeError, ValueError):
            setattr(st, name, getattr(fresh, name))
    for name in ("game_best", "fish_book", "ext_settings", "ext_usage"):
        if not isinstance(getattr(st, name), dict):
            setattr(st, name, getattr(fresh, name))
    for name in ("tutorials_seen", "auto_approved_tools", "watches"):
        if not isinstance(getattr(st, name), list):
            setattr(st, name, [])
    for name in ("skin", "hat", "ui_style", "ui_accent", "ui_size", "ui_font"):
        if not isinstance(getattr(st, name), str):
            setattr(st, name, getattr(fresh, name))
    for name in ("pets", "feeds", "plays", "best_round"):
        setattr(st, name, _int(getattr(st, name)))
    plays = st.game_plays if isinstance(st.game_plays, dict) else {}
    st.game_plays = {str(k): _int(v) for k, v in plays.items()}
    st.xp = _int(st.xp)
    limit = xp_limit(st)
    if st.xp > limit:
        log.warning("save: %d XP but the games played give at most %d - XP set to that", st.xp, limit)
        st.xp = limit
    return st


# ------------------------------------------------------------------ the converted-folder mark
def _folder_id(folder: Path) -> str:
    return hashlib.sha256(str(folder.resolve()).lower().encode("utf-8")).hexdigest()[:24]


def _converted(folder: Path) -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as k:
            return bool(winreg.QueryValueEx(k, _folder_id(folder))[0])
    except OSError:
        return False


def _mark_converted(folder: Path) -> None:
    try:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, _folder_id(folder), 0, winreg.REG_DWORD, 1)
    except OSError:
        log.warning("couldn't remember that the save is protected", exc_info=True)


def forget_folder(folder: Path) -> None:
    """The folder was deleted (uninstall with "delete its memory"): drop its mark."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _folder_id(folder))
    except OSError:
        pass


# ------------------------------------------------------------------ the save
def _decode(raw: bytes) -> SavedState:
    if raw.startswith(_MAGIC_WIN):
        plain = savecrypt.unprotect(raw[len(_MAGIC_WIN):])
    elif raw.startswith(_MAGIC_SIGNED):
        plain = savecrypt.unseal(raw[len(_MAGIC_SIGNED):])
    else:
        raise savecrypt.Unreadable("not a Pixel Cat save")
    try:
        return _from_dict(json.loads(plain.decode("utf-8")))
    except ValueError as e:
        raise savecrypt.Unreadable(f"damaged ({e})") from e


def _encode(st: SavedState) -> bytes:
    plain = json.dumps(asdict(st), separators=(",", ":")).encode("utf-8")
    try:
        return _MAGIC_WIN + savecrypt.protect(plain)
    except (OSError, AttributeError):
        log.exception("Windows couldn't protect the save - signing it instead")
        return _MAGIC_SIGNED + savecrypt.seal(plain)


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_state(folder: Path | None = None) -> SavedState:
    """The saved state: save.dat, else its backup, else the old state.json (converted once),
    else a new cat.  ``last_load`` says which: "save", "backup", "converted", "fresh", or
    "unreadable" (files there but none could be read - they are kept aside)."""
    global last_load
    d = folder or paths.data_dir()
    bad: list[Path] = []
    for name in (SAVE_NAME, BACKUP_NAME):
        path = d / name
        if not path.is_file():
            continue
        try:
            st = _decode(path.read_bytes())
        except (OSError, savecrypt.Unreadable) as e:
            log.warning("%s can't be read: %s", name, e)
            bad.append(path)
            continue
        last_load = "save" if name == SAVE_NAME else "backup"
        if bad:
            log.warning("loaded the backup save instead")
        return st
    if bad:                                     # changed by hand, or another account's save
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for path in bad:
            try:
                path.rename(path.with_name(f"unreadable-{stamp}-{path.name}"))
            except OSError:
                log.warning("couldn't move %s aside", path, exc_info=True)
        log.warning("no readable save - starting fresh (the unreadable files are kept)")
        last_load = "unreadable"
        return SavedState()
    legacy = d / LEGACY_NAME
    if legacy.is_file():
        if _converted(d):
            log.warning("ignoring %s: this folder already uses the protected save", LEGACY_NAME)
        else:
            try:
                st = _from_dict(json.loads(legacy.read_text(encoding="utf-8")))
            except (OSError, ValueError, savecrypt.Unreadable):
                log.exception("%s unreadable - starting fresh", LEGACY_NAME)
                st = SavedState()
            save_state(st, d, touch=False)
            try:
                _decode((d / SAVE_NAME).read_bytes())    # the protected copy works: the old file goes
                legacy.unlink()
                log.info("converted %s to the protected save", LEGACY_NAME)
            except (OSError, savecrypt.Unreadable):
                log.exception("the converted save can't be read back - keeping %s", LEGACY_NAME)
            last_load = "converted"
            return st
    last_load = "fresh"
    return SavedState()


def save_state(st: SavedState, folder: Path | None = None, touch: bool = True) -> None:
    """save.dat, then the same into save.bak (each written atomically: tmp file + replace)."""
    if touch:
        st.last_seen = time.time()
    d = folder or paths.data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        data = _encode(st)
        _write(d / SAVE_NAME, data)
        _write(d / BACKUP_NAME, data)
    except OSError:
        log.exception("could not save state")
        return
    if not _converted(d):
        _mark_converted(d)


# ------------------------------------------------------------------ export / import
def progress_of(st: SavedState) -> dict[str, Any]:
    return {k: getattr(st, k) for k in PROGRESS_FIELDS}


def export_progress(st: SavedState, path: Path) -> None:
    """Write the progress to a signed .pixelcat file (loads on any PC)."""
    from . import progress

    body = {"pixelcat_export": 1, "exported": datetime.now().isoformat(timespec="seconds"), **progress_of(st)}
    blob = savecrypt.seal(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    stage = progress.state(st.xp).stage
    text = (f"Pixel Cat progress - Cat Pass stage {stage}, saved {datetime.now():%Y-%m-%d %H:%M}.\n"
            f"Load it with Menu > Settings > Your progress > Import progress. Editing it makes it unreadable.\n"
            f"{_EXPORT_TAG}{base64.b64encode(blob).decode('ascii')}\n")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_export(path: Path) -> dict[str, Any]:
    """The progress in a .pixelcat file, checked.  Raises SaveError (a message for the user)."""
    try:
        if path.stat().st_size > 20_000_000:
            raise SaveError("That file is far too big to be a Pixel Cat save.")
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise SaveError(f"Couldn't open that file ({e.strerror or e}).") from e
    line = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith(_EXPORT_TAG)), None)
    if line is None:
        raise SaveError("That isn't a Pixel Cat save file.")
    try:
        plain = savecrypt.unseal(base64.b64decode(line[len(_EXPORT_TAG):], validate=True))
        data = json.loads(plain.decode("utf-8"))
        if not isinstance(data, dict) or data.get("pixelcat_export") != 1:
            raise savecrypt.Unreadable("not an export")
        return progress_of(_from_dict(data, only=PROGRESS_FIELDS))
    except (ValueError, savecrypt.Unreadable) as e:
        log.warning("import refused: %s", e)
        raise SaveError("That save file was changed or is damaged, so the cat won't load it.") from e


def apply_progress(st: SavedState, data: dict[str, Any]) -> None:
    """Put imported progress into the state (only the progress fields)."""
    for k in PROGRESS_FIELDS:
        if k in data:
            setattr(st, k, data[k])
