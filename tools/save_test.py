"""Protected save test (developer tool).  Everything happens in build/savetest (APPDATA is
moved there) and under its own registry key - the real save is never touched.

* The save: encrypted by Windows (not readable text), loads back; a hand-edited save.dat ->
  the backup is used; both edited -> a fresh start, the files kept aside; a save protected
  for someone else isn't read; the signed fallback works.
* The old state.json: converted once and deleted; a new one later is ignored.
* XP that the games played can't have given is cut down.
* Export/import: a signed file; an edited one is refused, so is a file that isn't a save;
  chat permissions and auto mode never come from it.
* In the app: Export writes the file; Import asks, backs up the current progress, then the
  skin, hat and XP follow; the save on disk has them.
Usage:  python tools/save_test.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "build" / "savetest"
shutil.rmtree(TEST, ignore_errors=True)
(TEST / "AppData").mkdir(parents=True)
os.environ["APPDATA"] = str(TEST / "AppData")          # the cat's folder for this test
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
sys.path.insert(0, str(ROOT))

import winreg  # noqa: E402

from pixelcat import paths, persistence, progress, savecrypt  # noqa: E402
from pixelcat.adapters.windows import dpapi, registry  # noqa: E402
from pixelcat.adapters.windows.platform import WindowsSaveMarks, WindowsSecretStore  # noqa: E402
from pixelcat.persistence import SavedState, load_state, save_state  # noqa: E402

registry.REG_PATH = r"Software\PixelCatSaveTest\ProtectedSaves"
STORAGE = {"secrets": WindowsSecretStore(), "marks": WindowsSaveMarks()}     # the app's (see bootstrap)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    results.append((name, ok, info))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""), flush=True)


def clear_reg() -> None:
    for key in (registry.REG_PATH, r"Software\PixelCatSaveTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass


def played(xp: int = 900) -> SavedState:
    return SavedState(xp=xp, plays=2, game_plays={"laser": 3, "mice": 2}, skin="ginger", best_round=7,
                      auto_mode=True, auto_approved_tools=["open_app"])


def files(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.is_file())


def main() -> int:  # noqa: C901 - a linear test script
    clear_reg()
    d = TEST / "a"
    d.mkdir()
    # Windows data protection ------------------------------------------------------------
    blob = dpapi.protect(b"hello cat")
    bad = bytearray(blob)
    bad[len(bad) // 2] ^= 1
    try:
        dpapi.unprotect(bytes(bad))
        refused = False
    except savecrypt.Unreadable:
        refused = True
    check("Windows protects data for this account; a changed byte is refused",
          dpapi.unprotect(blob) == b"hello cat" and refused)

    # the save ------------------------------------------------------------------------------
    save_state(played(), d, **STORAGE)
    raw = (d / "save.dat").read_bytes()
    st = load_state(d, **STORAGE)
    check("the save loads back, and isn't readable text", st.xp == 900 and st.skin == "ginger"
          and st.auto_mode and persistence.last_load == "save" and b"xp" not in raw and b"ginger" not in raw,
          persistence.last_load)
    check("...with a backup next to it", (d / "save.bak").read_bytes() == raw, str(files(d)))
    edited = bytearray(raw)
    edited[-5] ^= 0x20
    (d / "save.dat").write_bytes(bytes(edited))
    st = load_state(d, **STORAGE)
    check("a hand-edited save.dat: the backup is loaded, nothing is lost",
          st.xp == 900 and persistence.last_load == "backup", persistence.last_load)
    (d / "save.dat").write_text(json.dumps({"xp": 99999}), "utf-8")      # "just write the JSON"
    st = load_state(d, **STORAGE)
    check("a plain-text save.dat isn't accepted either", st.xp == 900 and persistence.last_load == "backup")
    (d / "save.bak").write_bytes(bytes(edited))
    st = load_state(d, **STORAGE)
    kept = [n for n in files(d) if n.startswith("unreadable-")]
    check("both edited: a fresh start, the unreadable files kept aside",
          st.xp == 0 and persistence.last_load == "unreadable" and len(kept) == 2
          and not (d / "save.dat").exists(), str(files(d)))
    real = savecrypt._ENTROPY
    savecrypt._ENTROPY = b"another account or PC"
    save_state(played(), d, **STORAGE)
    savecrypt._ENTROPY = real
    st = load_state(d, **STORAGE)
    check("a save protected for someone else (another account/PC) isn't read", st.xp == 0
          and persistence.last_load == "unreadable")
    real_protect = dpapi.protect

    def broken(_data: bytes) -> bytes:
        raise OSError(5, "no")
    dpapi.protect = broken                         # type: ignore[assignment]
    save_state(played(), d, **STORAGE)
    dpapi.protect = real_protect                   # type: ignore[assignment]
    st = load_state(d, **STORAGE)
    check("if Windows can't protect it, the save is signed instead (and still loads)",
          st.xp == 900 and (d / "save.dat").read_bytes().startswith(b"PXCAT1S"))

    # the old plain-text save ----------------------------------------------------------------
    e = TEST / "b"
    e.mkdir()
    (e / "state.json").write_text(json.dumps({"xp": 450, "game_plays": {"treats": 4}, "hat": "x", "pets": 3}),
                                  "utf-8")
    st = load_state(e, **STORAGE)
    check("the old state.json is converted once (kept: XP, plays) and deleted",
          st.xp == 450 and st.pets == 3 and persistence.last_load == "converted"
          and files(e) == ["save.bak", "save.dat"], str(files(e)))
    for n in ("save.dat", "save.bak"):
        (e / n).unlink()
    (e / "state.json").write_text(json.dumps({"xp": 600, "game_plays": {"treats": 4}}), "utf-8")
    st = load_state(e, **STORAGE)
    check("...a new state.json later is ignored (delete the save + write JSON doesn't work)",
          st.xp == 0 and persistence.last_load == "fresh")
    f = TEST / "c"
    f.mkdir()
    (f / "state.json").write_text(json.dumps({"xp": 99999, "game_plays": {"laser": 2}, "plays": 1}), "utf-8")
    st = load_state(f, **STORAGE)
    check("XP the games played can't have given is cut down", st.xp == 3 * persistence.MAX_XP_PER_PLAY,
          str(st.xp))

    # export / import -------------------------------------------------------------------------
    out = TEST / "mine.pixelcat"
    persistence.export_progress(played(), out)
    text = out.read_text("utf-8")
    data = persistence.read_export(out)
    check("an export loads back (XP, skin, plays, records), and isn't readable",
          data["xp"] == 900 and data["skin"] == "ginger" and data["best_round"] == 7 and '"xp"' not in text
          and "stage" in text.splitlines()[0])
    check("...without chat permissions or auto mode", "auto_mode" not in data and "auto_approved_tools" not in data)
    lines = text.splitlines()
    tag, body = lines[-1].split(":", 1)
    raw_b = bytearray(base64.b64decode(body))
    raw_b[20] ^= 1
    (TEST / "edited.pixelcat").write_text("\n".join(lines[:-1] + [tag + ":" + base64.b64encode(bytes(raw_b)).decode()]),
                                          "utf-8")
    (TEST / "notes.pixelcat").write_text("just some text", "utf-8")
    msgs = []
    for name in ("edited.pixelcat", "notes.pixelcat"):
        try:
            persistence.read_export(TEST / name)
            msgs.append("loaded!")
        except persistence.SaveError as err:
            msgs.append(str(err))
    check("an edited export is refused; so is a file that isn't a save",
          "changed" in msgs[0] and "isn't a Pixel Cat save" in msgs[1], " / ".join(msgs))
    forged = {"pixelcat_export": 1, "xp": 99999, "plays": 0, "game_plays": {}, "auto_mode": True}
    (TEST / "forged.pixelcat").write_text("x\n" + "PXCAT-EXPORT-1:" + base64.b64encode(
        savecrypt.seal(json.dumps(forged).encode())).decode(), "utf-8")
    data_f = persistence.read_export(TEST / "forged.pixelcat")
    check("a forged export (right key, impossible XP) gets the XP cut and no auto mode",
          data_f["xp"] == 0 and "auto_mode" not in data_f, str(data_f["xp"]))

    # in the app ----------------------------------------------------------------------------------
    from PySide6.QtWidgets import QApplication

    from pixelcat.app import PixelCatApp

    from pixelcat.bootstrap import build_platform, build_providers
    from pixelcat.adapters.windows import win32
    from pixelcat.logsetup import setup_logging
    from pixelcat.settings import load_config

    setup_logging(False)
    win32.set_dpi_awareness()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    st0 = SavedState(autostart_asked=True, xp=150, game_plays={"laser": 1}, plays=1, auto_mode=False)
    pet = PixelCatApp(app, load_config(), st0, selftest=1, platform=build_platform(), providers=build_providers())
    asked: list[str] = []
    answer = [str(TEST / "from-app")]
    pet._ask_file = lambda save, title, start: (asked.append(start), answer[0])[1]   # type: ignore[assignment]
    pet.export_progress()
    exp = TEST / "from-app.pixelcat"
    check("Export writes the file (in Documents by default, .pixelcat added)",
          exp.is_file() and persistence.read_export(exp)["xp"] == 150 and asked[0].endswith(".pixelcat"),
          asked[0])
    big = played(xp=progress.xp_to_reach(12))
    big.game_plays = {"laser": 40}
    persistence.export_progress(big, TEST / "big.pixelcat")
    answer[0] = str(TEST / "big.pixelcat")
    pet.import_progress()
    ask = [p for p in pet.popups.open if p.title == "Import progress?"]
    check("Import asks first (nothing changed yet)", bool(ask) and pet.saved.xp == 150)
    ask[-1]._finish(True)
    app.processEvents()
    backups = list((paths.data_dir() / "progress-backups").glob("before-import-*.pixelcat"))
    disk = load_state(**STORAGE)
    check("...then loads it: XP, skin and records follow; chat auto mode stays off",
          pet.saved.xp == big.xp and pet.saved.skin == "ginger" and pet.sprites.skin.key == "ginger"
          and pet.saved.best_round == 7 and pet.game.best == 7 and not pet.saved.auto_mode
          and not pet.saved.auto_approved_tools,
          f"xp {pet.saved.xp}, skin {pet.saved.skin}")
    check("...the progress from before is backed up, and the save on disk has the new one",
          len(backups) == 1 and persistence.read_export(backups[0])["xp"] == 150 and disk.xp == big.xp
          and not disk.auto_mode)
    answer[0] = str(TEST / "edited.pixelcat")
    n = len(pet.popups.open)
    pet.import_progress()
    check("importing an edited file: a message, nothing changes", len(pet.popups.open) == n + 1
          and pet.saved.xp == big.xp)
    pet._tell_unreadable_save()
    check("a save that can't be read: the cat says so (and how to import an export)",
          pet.popups.open[-1].title == "A fresh start" and "Import progress" in pet.popups.open[-1].text.text())
    items = {i.key: i for i in pet._menu_items()}
    sub = [c for c in items["settings"].children if c.key == "progress"]
    check("Menu > Settings > Your progress: Export and Import",
          bool(sub) and [c.key for c in sub[0].children] == ["export", "import"])
    for p in list(pet.popups.open):
        p.close()
    clear_reg()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
