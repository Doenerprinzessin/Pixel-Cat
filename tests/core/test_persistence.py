"""persistence: the protected save (save.dat / save.bak), the old state.json, the
converted-folder mark, export/import and the XP limit.  The secret store and the save marks
are fakes (the Windows ones are tested in tests/adapters/windows)."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from pixelcat import paths, persistence, progress, savecrypt
from pixelcat.domain.secrets import SecretStoreUnavailable
from pixelcat.persistence import SavedState

DATA = Path(__file__).parent / "data"
SIGNED = b"PXCAT1S\n"
WIN = b"PXCAT1W\n"


class Env:
    """The fake ports, and load/save with them."""

    def __init__(self, marks, secrets) -> None:  # type: ignore[no-untyped-def]
        self.marks, self.secrets = marks, secrets

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter((self.marks, self.secrets))

    def save(self, st: SavedState, folder: Path | None = None, touch: bool = True) -> None:
        persistence.save_state(st, folder, touch, secrets=self.secrets, marks=self.marks)

    def load(self, folder: Path | None = None) -> SavedState:
        return persistence.load_state(folder, secrets=self.secrets, marks=self.marks)


@pytest.fixture
def env(save_marks, dpapi, monkeypatch: pytest.MonkeyPatch) -> Env:  # type: ignore[no-untyped-def]
    """Fake save marks + fake DPAPI; ``last_load`` restored afterwards."""
    monkeypatch.setattr(persistence, "last_load", persistence.last_load)
    return Env(save_marks, dpapi)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "save"
    d.mkdir()
    return d


def played(xp: int = 900) -> SavedState:
    return SavedState(xp=xp, plays=2, game_plays={"laser": 3, "mice": 2}, skin="ginger", best_round=7,
                      auto_mode=True, auto_approved_tools=["open_app"])


def files(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


def mark_of(marks, folder: Path):  # type: ignore[no-untyped-def]
    return marks.marked.get(persistence._folder_id(folder))


def write_signed(path: Path, data) -> None:  # type: ignore[no-untyped-def]
    path.write_bytes(SIGNED + savecrypt.seal(json.dumps(data).encode("utf-8")))


# ---------------------------------------------------------------- save / load
def test_save_loads_back_and_is_not_plain_text(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    env.save(played(), folder)
    raw = (folder / "save.dat").read_bytes()
    st = env.load(folder)
    assert (st.xp, st.skin, st.best_round, st.auto_mode) == (900, "ginger", 7, True)
    assert st.game_plays == {"laser": 3, "mice": 2}
    assert persistence.last_load == "save"
    assert raw.startswith(WIN)
    assert b"ginger" not in raw and b"xp" not in raw


def test_save_writes_an_identical_backup_and_no_temp_files(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    env.save(played(), folder)
    assert files(folder) == ["save.bak", "save.dat"]
    assert (folder / "save.bak").read_bytes() == (folder / "save.dat").read_bytes()


def test_every_field_round_trips(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    st = SavedState(energy=0.25, hunger=0.5, mood=0.125, skin="snow", hat="party", xp=100, quiet=True,
                    autostart_asked=True, pos_x=-5, pos_y=300, pets=1, feeds=2, plays=3, best_round=4,
                    game_best={"laser": 9.5}, game_plays={"laser": 1}, tutorials_seen=["laser"],
                    fish_book={"carp": {"n": 1}}, model_download_ok=True, ui_style="classic", ui_accent="pink",
                    ui_size="large", ui_font="pixel", auto_approved_tools=["open_app"], auto_mode=True,
                    watches=[{"id": 1}], ext_settings={"a": 1}, ext_usage={"b": 2}, cat_name="Mochi",
                    chat_size=[200.0, 120.0], chat_full=True)
    env.save(st, folder, touch=False)
    assert asdict(env.load(folder)) == asdict(st)


def test_save_touch_updates_last_seen(env, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(persistence.time, "time", lambda: 1234.5)
    st = SavedState(last_seen=1.0)
    env.save(st, folder)
    assert st.last_seen == 1234.5
    st2 = SavedState(last_seen=1.0)
    env.save(st2, folder, touch=False)
    assert st2.last_seen == 1.0 and env.load(folder).last_seen == 1.0


def test_default_folder_is_the_data_dir(env) -> None:  # type: ignore[no-untyped-def]
    env.save(played())
    assert (paths.data_dir() / "save.dat").is_file()
    assert env.load().xp == 900


def test_nothing_saved_is_a_fresh_cat(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    st = env.load(folder)
    assert asdict(st) | {"last_seen": 0} == asdict(SavedState()) | {"last_seen": 0}
    assert persistence.last_load == "fresh"


def test_save_into_an_unwritable_place_does_not_raise(env, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    env.save(played(), blocker / "sub")
    assert marks.marked == {}


# ---------------------------------------------------------------- damaged / changed saves
def test_hand_edited_save_loads_the_backup(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    env.save(played(), folder)
    raw = bytearray((folder / "save.dat").read_bytes())
    raw[-5] ^= 0x20
    (folder / "save.dat").write_bytes(bytes(raw))
    st = env.load(folder)
    assert st.xp == 900 and persistence.last_load == "backup"


@pytest.mark.parametrize("content", [
    json.dumps({"xp": 99999}).encode(),
    b"",
    WIN + b"garbage",
    SIGNED + b"garbage",
    SIGNED + savecrypt.seal(b"not json"),
    SIGNED + savecrypt.seal(b"[1, 2, 3]"),
    SIGNED + savecrypt.seal(b"\xff\xfe"),
], ids=["plain-json", "empty", "win-garbage", "signed-garbage", "signed-not-json", "signed-not-dict", "signed-not-utf8"])
def test_unreadable_save_dat_falls_back_to_the_backup(env, folder: Path, content: bytes) -> None:  # type: ignore[no-untyped-def]
    env.save(played(), folder)
    (folder / "save.dat").write_bytes(content)
    st = env.load(folder)
    assert st.xp == 900 and persistence.last_load == "backup"


def test_both_unreadable_starts_fresh_and_keeps_the_files_aside(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    env.save(played(), folder)
    for name in ("save.dat", "save.bak"):
        (folder / name).write_bytes(b"edited")
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "unreadable"
    kept = files(folder)
    assert len(kept) == 2
    assert all(re.fullmatch(r"unreadable-\d{8}-\d{6}-save\.(dat|bak)", n) for n in kept), kept
    assert all((folder / n).read_bytes() == b"edited" for n in kept)


def test_only_an_unreadable_backup_starts_fresh(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    (folder / "save.bak").write_bytes(b"edited")
    assert env.load(folder).xp == 0
    assert persistence.last_load == "unreadable"
    assert not (folder / "save.bak").exists()


def test_a_save_from_another_account_is_unreadable(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    _, dpapi = env
    dpapi.account = b"someone else"
    env.save(played(), folder)
    dpapi.account = b"this account"
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "unreadable"


def test_unreadable_files_that_cannot_be_moved_are_left(env, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    (folder / "save.dat").write_bytes(b"edited")

    def refuse(self, target):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "in use")

    monkeypatch.setattr(Path, "rename", refuse)
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "unreadable"
    assert files(folder) == ["save.dat"]


# ---------------------------------------------------------------- signed fallback
@pytest.mark.parametrize("error", [OSError(5, "no"), AttributeError("no DPAPI here")], ids=["oserror", "attributeerror"])
def test_save_is_signed_when_windows_cannot_protect_it(env, folder: Path, error: Exception) -> None:  # type: ignore[no-untyped-def]
    _, dpapi = env
    dpapi.protect_error = error
    env.save(played(), folder)
    raw = (folder / "save.dat").read_bytes()
    assert raw.startswith(SIGNED)
    assert b"ginger" not in raw
    dpapi.protect_error = None
    assert env.load(folder).xp == 900 and persistence.last_load == "save"


# ---------------------------------------------------------------- the secret store can't be used right now
def test_a_locked_secret_store_keeps_the_save_and_writes_nothing_this_run(env, folder: Path,  # type: ignore[no-untyped-def]
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "_locked", set())
    _, dpapi = env
    env.save(played(), folder)
    before = {n: (folder / n).read_bytes() for n in files(folder)}
    dpapi.unprotect_error = SecretStoreUnavailable("the keychain is locked")
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "locked"
    assert {n: (folder / n).read_bytes() for n in files(folder)} == before     # nothing renamed or changed
    dpapi.unprotect_error = None
    env.save(SavedState(), folder)                                           # (a new cat must not overwrite it)
    assert {n: (folder / n).read_bytes() for n in files(folder)} == before
    other = folder.parent / "other"
    env.save(played(450), other)                                             # (only that folder is locked)
    assert env.load(other).xp == 450


def test_a_locked_secret_store_never_makes_the_save_weaker(env, folder: Path,  # type: ignore[no-untyped-def]
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "_locked", set())
    _, dpapi = env
    env.save(played(), folder)
    before = (folder / "save.dat").read_bytes()
    dpapi.protect_error = SecretStoreUnavailable("the keychain is locked")
    env.save(played(450), folder)
    assert (folder / "save.dat").read_bytes() == before                     # not signed instead: just not saved
    assert persistence.is_locked(folder)
    monkeypatch.setattr(persistence, "_locked", set())                       # (a restart)
    dpapi.protect_error = None
    env.save(played(450), folder)
    assert env.load(folder).xp == 450 and persistence.last_load == "save"


def test_a_first_save_the_secret_store_refuses_stops_asking_for_the_run(env, folder: Path,  # type: ignore[no-untyped-def]
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "_locked", set())
    _, dpapi = env
    asked: list[bytes] = []
    protect = dpapi.protect

    def counted(data: bytes) -> bytes:
        asked.append(data)
        return protect(data)
    monkeypatch.setattr(dpapi, "protect", counted)
    assert env.load(folder).xp == 0 and persistence.last_load == "fresh" and not persistence.is_locked(folder)
    dpapi.protect_error = SecretStoreUnavailable("the user denied the keychain")
    env.save(played(), folder)
    assert persistence.is_locked(folder) and files(folder) == []
    dpapi.protect_error = None
    env.save(played(), folder)
    env.save(played(), folder)
    assert len(asked) == 1 and files(folder) == []                       # asked once, not after every "Deny"
    assert not persistence.is_locked(folder.parent / "other")


def test_signed_save_loads_on_another_account(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    _, dpapi = env
    dpapi.protect_error = OSError(5, "no")
    env.save(played(), folder)
    dpapi.account = b"someone else"
    assert env.load(folder).xp == 900


def test_golden_signed_save_still_loads(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    """A save.dat in the signed format, written by the code of PR 0, must keep loading."""
    (folder / "save.dat").write_bytes((DATA / "signed_save_v1.dat").read_bytes())
    st = env.load(folder)
    assert persistence.last_load == "save"
    assert (st.xp, st.skin, st.hat, st.best_round, st.plays, st.pets, st.feeds) == (900, "ginger", "party", 7, 2, 5, 4)
    assert st.game_plays == {"laser": 3, "mice": 2} and st.game_best == {"laser": 12.5}
    assert (st.auto_mode, st.auto_approved_tools, st.cat_name) == (True, ["open_app"], "Mochi")
    assert (st.last_seen, st.pos_x, st.pos_y, st.energy) == (1700000000.0, 10, 20, 0.5)
    assert st.fish_book == {"carp": {"n": 2}} and st.chat_size == [200.0, 150.0]
    assert (st.ui_style, st.tutorials_seen) == ("classic", ["laser"])


# ---------------------------------------------------------------- checking what was read
@pytest.mark.parametrize("data, expect", [
    ({"energy": "abc"}, {"energy": 0.8}),
    ({"energy": 5, "hunger": -1, "mood": "0.3"}, {"energy": 1.0, "hunger": 0.0, "mood": 0.3}),
    ({"skin": 5, "hat": None, "cat_name": ["x"], "ui_style": 1}, {"skin": "charcoal", "hat": "", "cat_name": "",
                                                                  "ui_style": "catchat"}),
    ({"game_best": [], "fish_book": "x", "ext_settings": 1, "ext_usage": None},
     {"game_best": {}, "fish_book": {}, "ext_settings": {}, "ext_usage": {}}),
    ({"tutorials_seen": "laser", "auto_approved_tools": {}, "watches": 3, "chat_size": "big"},
     {"tutorials_seen": [], "auto_approved_tools": [], "watches": [], "chat_size": []}),
    ({"chat_full": "yes"}, {"chat_full": False}),
    ({"chat_full": 1}, {"chat_full": False}),
    ({"chat_full": True}, {"chat_full": True}),
    ({"pets": -3, "feeds": "x", "plays": 2.9, "best_round": "7"}, {"pets": 0, "feeds": 0, "plays": 2, "best_round": 7}),
    ({"game_plays": {"laser": "3", 1: -2, "mice": None}}, {"game_plays": {"laser": 3, "1": 0, "mice": 0}}),
    ({"game_plays": ["laser"]}, {"game_plays": {}}),
    ({"no_such_field": 1, "version": 9}, {"version": 9}),
    ({"xp": -50}, {"xp": 0}),
    ({"xp": "lots"}, {"xp": 0}),
    ({"xp": float("inf")}, {"xp": 0}),
], ids=lambda v: json.dumps(v, default=str)[:40] if isinstance(v, dict) else None)
def test_loaded_values_are_checked(env, folder: Path, data: dict, expect: dict) -> None:  # type: ignore[no-untyped-def]
    write_signed(folder / "save.dat", data)
    st = env.load(folder)
    assert {k: getattr(st, k) for k in expect} == expect
    assert not hasattr(st, "no_such_field")


@pytest.mark.parametrize("xp, plays, game_plays, expect", [
    (900, 2, {"laser": 3, "mice": 2}, 900),
    (99999, 1, {"laser": 2}, 3 * 200),
    (201, 1, {}, 200),
    (200, 0, {"laser": 1}, 200),
    (50, 0, {}, 0),
])
def test_xp_is_cut_to_what_the_games_played_could_give(env, folder: Path, xp: int, plays: int,  # type: ignore[no-untyped-def]
                                                       game_plays: dict, expect: int) -> None:
    write_signed(folder / "save.dat", {"xp": xp, "plays": plays, "game_plays": game_plays})
    assert env.load(folder).xp == expect


def test_xp_limit() -> None:
    assert persistence.xp_limit(SavedState(plays=2, game_plays={"a": 3, "b": 4})) == 9 * persistence.MAX_XP_PER_PLAY
    assert persistence.xp_limit(SavedState()) == 0


# ---------------------------------------------------------------- the old state.json
def test_state_json_is_converted_once_and_deleted(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    (folder / "state.json").write_text(json.dumps({"xp": 450, "game_plays": {"treats": 4}, "hat": "x", "pets": 3,
                                                   "last_seen": 42.0}), "utf-8")
    st = env.load(folder)
    assert (st.xp, st.pets, st.hat, st.last_seen) == (450, 3, "x", 42.0)
    assert persistence.last_load == "converted"
    assert files(folder) == ["save.bak", "save.dat"]
    assert env.load(folder).xp == 450 and persistence.last_load == "save"
    assert mark_of(marks, folder) == 1


def test_a_new_state_json_in_a_converted_folder_is_ignored(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    (folder / "state.json").write_text(json.dumps({"xp": 450, "game_plays": {"treats": 4}}), "utf-8")
    env.load(folder)
    for n in ("save.dat", "save.bak"):
        (folder / n).unlink()
    (folder / "state.json").write_text(json.dumps({"xp": 600, "game_plays": {"treats": 4}}), "utf-8")
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "fresh"
    assert (folder / "state.json").exists()


def test_saving_marks_the_folder_as_converted(env, folder: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    other = tmp_path / "other"
    env.save(played(), folder)
    assert mark_of(marks, folder) == 1
    assert mark_of(marks, other) is None
    (other).mkdir()
    (other / "state.json").write_text(json.dumps({"pets": 2}), "utf-8")
    assert env.load(other).pets == 2 and persistence.last_load == "converted"


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", "\"text\""], ids=["broken", "list", "string"])
def test_unreadable_state_json_converts_to_a_fresh_cat(env, folder: Path, content: str) -> None:  # type: ignore[no-untyped-def]
    (folder / "state.json").write_text(content, "utf-8")
    st = env.load(folder)
    assert st.xp == 0 and persistence.last_load == "converted"
    assert files(folder) == ["save.bak", "save.dat"]


def test_state_json_is_kept_when_the_converted_save_cannot_be_read_back(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    _, dpapi = env
    dpapi.unprotect_error = savecrypt.Unreadable("broken DPAPI")
    (folder / "state.json").write_text(json.dumps({"pets": 3}), "utf-8")
    st = env.load(folder)
    assert st.pets == 3 and persistence.last_load == "converted"
    assert (folder / "state.json").exists()


def test_without_a_registry_conversion_still_works(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    marks.fail = PermissionError
    (folder / "state.json").write_text(json.dumps({"pets": 3}), "utf-8")
    assert env.load(folder).pets == 3
    assert files(folder) == ["save.bak", "save.dat"]
    for n in ("save.dat", "save.bak"):
        (folder / n).unlink()
    (folder / "state.json").write_text(json.dumps({"pets": 5}), "utf-8")
    assert env.load(folder).pets == 5               # (not remembered: converted again)


def test_folder_mark_name_format(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    """The registry value name must stay the same, or converted folders are forgotten."""
    marks, _ = env
    env.save(played(), folder)
    name = hashlib.sha256(str(folder.resolve()).lower().encode("utf-8")).hexdigest()[:24]
    assert marks.marked == {name: 1}


def test_forget_folder_removes_the_mark(env, folder: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    env.save(played(), folder)
    env.save(played(), tmp_path / "other")
    persistence.forget_folder(folder, marks)
    assert mark_of(marks, folder) is None
    assert mark_of(marks, tmp_path / "other") == 1


def test_forget_folder_without_a_mark_is_fine(env, folder: Path) -> None:  # type: ignore[no-untyped-def]
    marks, _ = env
    persistence.forget_folder(folder, marks)
    marks.fail = PermissionError
    persistence.forget_folder(folder, marks)


# ---------------------------------------------------------------- export / import
def test_export_loads_back_without_chat_permissions(tmp_path: Path) -> None:
    out = tmp_path / "mine.pixelcat"
    persistence.export_progress(played(), out)
    data = persistence.read_export(out)
    assert data == persistence.progress_of(played())
    assert set(data) == set(persistence.PROGRESS_FIELDS)
    assert "auto_mode" not in data and "auto_approved_tools" not in data


def test_export_file_format(tmp_path: Path) -> None:
    out = tmp_path / "mine.pixelcat"
    persistence.export_progress(played(), out)
    lines = out.read_text("utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].startswith(f"Pixel Cat progress - Cat Pass stage {progress.state(900).stage}, saved ")
    assert "Import progress" in lines[1]
    assert lines[2].startswith("PXCAT-EXPORT-1:")
    body = json.loads(savecrypt.unseal(base64.b64decode(lines[2].split(":", 1)[1])))
    assert body["pixelcat_export"] == 1 and body["xp"] == 900 and "exported" in body
    assert '"xp"' not in out.read_text("utf-8")
    assert not (tmp_path / "mine.pixelcat.tmp").exists()


def test_golden_export_still_imports() -> None:
    data = persistence.read_export(DATA / "export_v1.pixelcat")
    assert (data["xp"], data["skin"], data["hat"], data["best_round"]) == (900, "ginger", "party", 7)
    assert data["game_plays"] == {"laser": 3, "mice": 2} and data["fish_book"] == {"carp": {"n": 2}}
    assert (data["pets"], data["feeds"], data["plays"], data["ui_style"]) == (5, 4, 2, "classic")


def test_export_tag_line_found_among_other_lines(tmp_path: Path) -> None:
    src = tmp_path / "a.pixelcat"
    persistence.export_progress(played(), src)
    tag = src.read_text("utf-8").splitlines()[-1]
    other = tmp_path / "b.pixelcat"
    other.write_text(f"a note\r\n\r\n   {tag}   \r\nmore text\r\n", "utf-8")
    assert persistence.read_export(other)["xp"] == 900


def _edited(path: Path) -> None:
    lines = path.read_text("utf-8").splitlines()
    tag, body = lines[-1].split(":", 1)
    raw = bytearray(base64.b64decode(body))
    raw[20] ^= 1
    path.write_text("\n".join(lines[:-1] + [tag + ":" + base64.b64encode(bytes(raw)).decode()]), "utf-8")


@pytest.mark.parametrize("make, message", [
    (lambda p: (persistence.export_progress(played(), p), _edited(p)), "changed or is damaged"),
    (lambda p: p.write_text("just some text", "utf-8"), "isn't a Pixel Cat save"),
    (lambda p: p.write_text("PXCAT-EXPORT-1:not base64!!", "utf-8"), "changed or is damaged"),
    (lambda p: p.write_text("PXCAT-EXPORT-1:" + base64.b64encode(savecrypt.seal(b"[1]")).decode(), "utf-8"),
     "changed or is damaged"),
    (lambda p: p.write_text("PXCAT-EXPORT-1:" + base64.b64encode(
        savecrypt.seal(json.dumps({"pixelcat_export": 2, "xp": 5}).encode())).decode(), "utf-8"), "changed or is damaged"),
    (lambda p: p.write_bytes(b"PXCAT-EXPORT-1:" + base64.b64encode(savecrypt.seal(b"\xff\xfe"))), "changed or is damaged"),
], ids=["edited", "not-a-save", "bad-base64", "not-a-dict", "wrong-version", "not-utf8"])
def test_read_export_refuses(tmp_path: Path, make, message: str) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "x.pixelcat"
    make(p)
    with pytest.raises(persistence.SaveError, match=message):
        persistence.read_export(p)


def test_read_export_missing_file(tmp_path: Path) -> None:
    with pytest.raises(persistence.SaveError, match="Couldn't open that file"):
        persistence.read_export(tmp_path / "missing.pixelcat")


def test_read_export_refuses_a_huge_file(tmp_path: Path) -> None:
    p = tmp_path / "huge.pixelcat"
    with p.open("wb") as fh:
        fh.truncate(20_000_001)
    with pytest.raises(persistence.SaveError, match="far too big"):
        persistence.read_export(p)


def test_forged_export_gets_impossible_xp_cut_and_no_permissions(tmp_path: Path) -> None:
    forged = {"pixelcat_export": 1, "xp": 99999, "plays": 0, "game_plays": {}, "auto_mode": True,
              "auto_approved_tools": ["run"], "model_download_ok": True}
    p = tmp_path / "forged.pixelcat"
    p.write_text("x\nPXCAT-EXPORT-1:" + base64.b64encode(savecrypt.seal(json.dumps(forged).encode())).decode(), "utf-8")
    data = persistence.read_export(p)
    assert data["xp"] == 0
    assert set(data) == set(persistence.PROGRESS_FIELDS)


def test_apply_progress_only_touches_progress_fields() -> None:
    st = SavedState(auto_mode=False, cat_name="Mochi", energy=0.3)
    persistence.apply_progress(st, {"xp": 400, "skin": "snow", "auto_mode": True, "cat_name": "Evil", "energy": 1.0})
    assert (st.xp, st.skin) == (400, "snow")
    assert (st.auto_mode, st.cat_name, st.energy) == (False, "Mochi", 0.3)


def test_import_round_trip_into_a_state(tmp_path: Path) -> None:
    p = tmp_path / "x.pixelcat"
    persistence.export_progress(played(), p)
    st = SavedState()
    persistence.apply_progress(st, persistence.read_export(p))
    assert (st.xp, st.skin, st.best_round, st.game_plays) == (900, "ginger", 7, {"laser": 3, "mice": 2})
    assert not st.auto_mode and st.auto_approved_tools == []
