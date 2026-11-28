"""pixelcat.llm.watch: watches, their checks and the manager (desktop and notification feed faked)."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

from llm_agent.agent_fakes import OWN_PID, FakeFeed
from pixelcat.domain import Element, Notification, Snapshot
from pixelcat.llm import watch as watch_mod
from pixelcat.llm.watch import MAX_ACTIVE, Checker, Watch, WatchError, WatchManager


@pytest.fixture
def checker(desk) -> Checker:  # type: ignore[no-untyped-def]
    c = Checker(OWN_PID, platform=desk.platform)
    c.feed = FakeFeed()
    return c


@pytest.fixture
def texts(monkeypatch):  # type: ignore[no-untyped-def]
    """hwnd -> the text _window_text reads (None = unreadable)."""
    by_hwnd: dict = {}
    monkeypatch.setattr(watch_mod, "_window_text", lambda a11y, hwnd, timeout=8.0: by_hwnd.get(hwnd))
    return by_hwnd


def W(kind: str, **kw) -> Watch:  # type: ignore[no-untyped-def]
    kw.setdefault("until", time.time() + 3600)
    return Watch(kw.pop("id", 1), kind, kw.pop("label", f"a {kind}"), **kw)


# ------------------------------------------------------------------ Watch
def test_to_dict_and_back_drops_private_state() -> None:
    w = W("file", then="read it", path="C:/x", state={"mode": "folder", "_sizes": {"a": 1}})
    d = w.to_dict()
    assert d["state"] == {"mode": "folder"} and d["then"] == "read it" and d["kind"] == "file"
    back = Watch.from_dict({**d, "unknown_key": 1})
    assert back == Watch(**{**d, "state": {"mode": "folder"}})


def test_short() -> None:
    assert W("time", label="the time 17:00").short() == "the time 17:00"
    now = datetime.now().replace(hour=23, minute=0, second=0, microsecond=0)
    assert W("window_closed", label="setup to close", until=now.timestamp()).short() == "setup to close (until 23:00)"
    later = now.replace(day=1).timestamp() + 40 * 86400
    assert W("file", label="x", until=later).short() == f"x (until {datetime.fromtimestamp(later):%a %H:%M})"


def test_norm() -> None:
    assert watch_mod._norm("  Your \n TURN\tnow ") == "your turn now"


def test_window_text_reads_names_values_and_the_document(desk) -> None:  # type: ignore[no-untyped-def]
    desk.snapshots[5] = Snapshot(5, (0, 0, 1, 1), [
        Element(1, "text", "Status", (0, 0, 1, 1), value="Exporting"),
        Element(2, "edit", "", (0, 0, 1, 1), value="hunter2", password=True),
        Element(3, "button", "", (0, 0, 1, 1))])
    desk.docs[5] = "Body text"
    assert watch_mod._window_text(desk.platform.accessibility, 5) == "Status Exporting\nBody text"
    assert watch_mod._window_text(desk.platform.accessibility, 6) is None                        # UI Automation failed


# ------------------------------------------------------------------ baselines
def test_baseline_notification(checker: Checker) -> None:
    checker.feed.items = [Notification(41, "MSTeams_8wekyb3d8bbwe!MSTeams", ["hi"])]
    checker.feed.badge = {"MSTeams_8wekyb3d8bbwe!MSTeams": "2", "Outlook": "5"}
    w = W("notification", app="teams")
    assert checker.baseline(w) == ""
    assert w.state == {"last": 41, "badges": {"MSTeams_8wekyb3d8bbwe!MSTeams": "2"}}
    w = W("notification")
    checker.baseline(w)
    assert w.state["badges"] == {}                                 # any app: no badge watching
    checker.feed.broken = True
    with pytest.raises(WatchError, match="^I can't read Windows notifications on this PC"):
        checker.baseline(W("notification"))


def test_baseline_windows(checker: Checker, desk) -> None:  # type: ignore[no-untyped-def]
    for i in range(7):
        desk.add_window(10 + i, f"Setup {i}", "setup.exe")
    w = W("window_closed", app="setup")
    checker.baseline(w)
    assert w.state == {"hwnds": [10, 11, 12, 13, 14], "names": [f"setup: Setup {i}" for i in range(5)]}
    w = W("window_closed", app="setup", text="setup 3")
    checker.baseline(w)
    assert w.state["hwnds"] == [13]
    with pytest.raises(WatchError, match="^no open window matches 'zoom' - it may be closed already$"):
        checker.baseline(W("window_closed", app="zoom"))
    w = W("window_opened", text="Setup 6")
    checker.baseline(w)
    assert w.state == {"hwnds": [16]}


def test_baseline_window_text(checker: Checker, desk, texts) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(10, "Exporter", "exporter.exe")
    texts[10] = "Export\n  Exporting 40%  \nCancel"
    assert checker.baseline(W("window_text", app="exporter", text="exporting")) == \
        'exporter: Exporter already shows: "Exporting 40%"'
    assert checker.baseline(W("window_text", app="exporter", text="done", gone=True)) == \
        "'done' isn't shown in exporter: Exporter right now."
    assert checker.baseline(W("window_text", app="exporter", text="done")) == ""
    assert checker.baseline(W("window_text", app="nothing", text="x", gone=True)) == \
        "No window matches 'nothing' right now, so 'x' isn't shown anywhere."
    with pytest.raises(WatchError, match="^no open window matches 'nothing' - open it first, or use window_opened$"):
        checker.baseline(W("window_text", app="nothing", text="x"))
    texts[10] = None
    with pytest.raises(WatchError, match=r"^I can't read the text of exporter: Exporter \(the app doesn't share it\)"):
        checker.baseline(W("window_text", app="exporter", text="x"))


def test_baseline_window_text_of_a_minimized_window(checker: Checker, desk, texts) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(10, "Exporter", "exporter.exe", minimized=True)
    w = W("window_text", app="exporter", text="x")
    assert checker.baseline(w) == "" and w.state["note"].startswith("the window is minimized")


def test_baseline_files(checker: Checker, tmp_path: Path) -> None:
    w = W("file", path=str(tmp_path))
    checker.baseline(w)
    assert w.state == {"mode": "folder"}
    f = tmp_path / "a.txt"
    w = W("file", path=str(f))
    checker.baseline(w)
    assert w.state == {"mode": "appear"}
    f.write_text("abc")
    checker.baseline(w)
    assert w.state["mode"] == "change" and w.state["sig"] == [3, f.stat().st_mtime]


def test_baseline_time_has_nothing_to_record(checker: Checker) -> None:
    w = W("time", at=time.time() + 60)
    assert checker.baseline(w) == "" and w.state == {}


# ------------------------------------------------------------------ checks
def test_check_time(checker: Checker) -> None:
    assert checker.check(W("time", at=time.time() + 60)) == ""
    assert re.fullmatch(r"It is \d\d:\d\d now\.", checker.check(W("time", at=time.time() - 5)))
    late = time.time() - 600
    assert checker.check(W("time", at=late)).startswith(f"It was due at {datetime.fromtimestamp(late):%H:%M} but I "
                                                        "couldn't wake up then")


def test_check_notification(checker: Checker) -> None:
    w = W("notification", app="teams", text="anna", state={"last": 5, "badges": {}})
    checker.feed.items = [Notification(6, "Outlook", ["Anna"]), Notification(7, "MSTeams!App", ["Bob", "hello"])]
    assert checker.check(w) == "" and w.state["last"] == 7
    checker.feed.items.append(Notification(8, "MSTeams!App", ["Anna", "are you coming?"],
                                           datetime(2026, 1, 1, 9, 30)))
    assert checker.check(w) == 'New notification from Microsoft Teams at 09:30: "Anna" - "are you coming?"'
    w = W("notification", state={"last": 0})
    checker.feed.items = [Notification(i, "App", [str(i)], datetime(2026, 1, 1, 9, 0)) for i in range(1, 6)]
    assert checker.check(w).endswith('App at 09:00: "3" (+2 more)')


def test_check_notification_badges(checker: Checker) -> None:
    w = W("notification", app="teams", text="Anna", state={"last": 0, "badges": {"MSTeams!App": "1"}})
    checker.feed.badge = {"MSTeams!App": "3", "Outlook": "9"}
    assert checker.check(w) == ""                                  # give the pop-up a moment
    assert w.state["_badge"] == ["MSTeams!App", "1", "3"]
    assert checker.check(w) == ("Microsoft Teams now shows 3 unread on its icon (was 1), but no notification text came "
                                "with it so I can't tell whether it is from/about 'Anna' - look at the app to see what "
                                "it is.")
    w = W("notification", app="teams", state={"last": 0, "badges": {}})
    checker.feed.badge = {"MSTeams!App": "newMessage"}
    assert checker.check(w) == ""
    assert checker.check(w) == ("Microsoft Teams now shows a new item on its icon (was 0), but no notification text "
                                "came with it - look at the app to see what it is.")


def test_badge_with_a_non_matching_popup_is_not_reported(checker: Checker) -> None:
    w = W("notification", app="teams", text="Anna", state={"last": 0, "badges": {}})
    checker.feed.items = [Notification(1, "MSTeams!App", ["Bob"])]
    checker.feed.badge = {"MSTeams!App": "1"}
    assert checker.check(w) == "" and "_badge" not in w.state
    assert checker.check(w) == ""


def test_check_windows(checker: Checker, desk) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(10, "Setup", "setup.exe")
    w = W("window_closed", app="setup", state={"hwnds": [10, 11], "names": ["setup: Setup", "setup: Two"]})
    assert checker.check(w) == ""
    desk.alive.discard(10)
    assert checker.check(w) == "The window setup: Setup closed (and the others)."
    assert checker.check(W("window_closed", app="x", state={"hwnds": [99]})) == "The window x closed."
    w = W("window_opened", app="paint", state={"hwnds": []})
    assert checker.check(w) == ""
    desk.add_window(20, "Untitled - Paint", "mspaint.exe")
    assert checker.check(w) == "A new window opened: mspaint: Untitled - Paint."
    assert checker.check(w) == "" and w.state["hwnds"] == [20]


def test_check_window_text(checker: Checker, desk, texts) -> None:  # type: ignore[no-untyped-def]
    w = W("window_text", app="exporter", text="done")
    assert checker.check(w) == ""
    desk.add_window(10, "Exporter", "exporter.exe")
    texts[10] = "Exporting..."
    assert checker.check(w) == ""
    texts[10] = "Export DONE!"
    assert checker.check(w) == 'exporter: Exporter now shows: "Export DONE!"'


def test_check_window_text_gone(checker: Checker, desk, texts) -> None:  # type: ignore[no-untyped-def]
    desk.add_window(10, "Exporter", "exporter.exe")
    w = W("window_text", app="exporter", text="Exporting", gone=True)
    texts[10] = "Exporting 90%"
    assert checker.check(w) == "" and w.state["_absent"] == 0
    texts[10] = "Ready"
    assert checker.check(w) == ""                                  # once could be a flicker
    assert checker.check(w) == "'Exporting' is no longer shown in exporter: Exporter."
    desk.alive.discard(10)
    assert checker.check(w) == "The window 'exporter' is closed now (so 'Exporting' isn't shown any more)."


def test_check_file_appears_once_complete(checker: Checker, tmp_path: Path) -> None:
    f = tmp_path / "result.txt"
    w = W("file", path=str(f), state={"mode": "appear"})
    assert checker.check(w) == ""
    f.write_text("x" * 2048)
    assert checker.check(w) == ""                                  # wait until it stops growing
    assert checker.check(w) == f"{f} is there now (2 KB)."


def test_check_file_changes(checker: Checker, tmp_path: Path) -> None:
    f = tmp_path / "doc.txt"
    f.write_text("a")
    st = f.stat()
    w = W("file", path=str(f), state={"mode": "change", "sig": [st.st_size, st.st_mtime]})
    assert checker.check(w) == ""
    f.write_text("abc")
    os.utime(f, (st.st_mtime + 10, st.st_mtime + 10))
    assert checker.check(w) == ""
    assert checker.check(w).startswith(f"{f} changed (0 KB, saved ")


def test_check_new_file_in_a_folder(checker: Checker, tmp_path: Path) -> None:
    w = W("file", path=str(tmp_path), text="report", started=time.time() - 0.5, state={"mode": "folder"})
    (tmp_path / "movie.crdownload").write_bytes(b"x" * 10)
    (tmp_path / "~$report.docx").write_bytes(b"x" * 10)
    (tmp_path / "photo.jpg").write_bytes(b"x" * 10)
    (tmp_path / "report.pdf").write_bytes(b"x" * 4096)
    (tmp_path / "empty report.txt").write_bytes(b"")
    (tmp_path / "report dir").mkdir()
    assert checker.check(w) == ""
    assert checker.check(w) == f"New file in {tmp_path}: report.pdf (4 KB)"
    assert checker.check(W("file", path=str(tmp_path / "gone"), state={"mode": "folder"})) == ""


def test_check_folder_ignores_files_from_before(checker: Checker, tmp_path: Path) -> None:
    (tmp_path / "old.pdf").write_bytes(b"x")
    w = W("file", path=str(tmp_path), started=time.time() + 60, state={"mode": "folder"})
    checker.check(w)
    assert checker.check(w) == ""


# ------------------------------------------------------------------ manager
@pytest.fixture
def mgr(monkeypatch, desk) -> WatchManager:  # type: ignore[no-untyped-def]
    m = WatchManager(OWN_PID, platform=desk.platform)
    m.checker.feed = FakeFeed()
    monkeypatch.setattr(m, "_ensure_thread", lambda: None)
    return m


def test_ids_add_cancel(mgr: WatchManager) -> None:
    changed: list = []
    mgr.changed.connect(lambda: changed.append(1))
    assert mgr.new_id() == 1 and mgr.new_id() == 2
    mgr.add(W("time", id=7, at=time.time() + 60))
    assert mgr.new_id() == 8
    mgr.add(W("time", id=8, at=time.time() + 600))
    assert [w.id for w in mgr.active()] == [7, 8] and len(changed) == 2
    assert [w.id for w in mgr.cancel(7)] == [7] and mgr.cancel(7) == []
    assert len(changed) == 3
    assert [w.id for w in mgr.cancel(None)] == [8] and not mgr.active()


def test_at_most_ten(mgr: WatchManager) -> None:
    for i in range(MAX_ACTIVE):
        mgr.add(W("time", id=i + 1))
    with pytest.raises(WatchError, match="already watching 10 things"):
        mgr.add(W("time", id=99))


def test_duplicate_of(mgr: WatchManager) -> None:
    now = time.time()
    a = W("notification", id=1, app="Teams ", text="Anna")
    t = W("time", id=2, at=now + 600)
    mgr.add(a)
    mgr.add(t)
    assert mgr.duplicate_of(W("notification", app="teams", text="anna")) is a
    assert mgr.duplicate_of(W("notification", app="teams", text="bob")) is None
    assert mgr.duplicate_of(W("notification", app="teams", text="anna", gone=True)) is None
    assert mgr.duplicate_of(W("time", at=now + 700)) is t
    assert mgr.duplicate_of(W("time", at=now + 800)) is None


def test_save_and_restore(mgr: WatchManager, desk) -> None:  # type: ignore[no-untyped-def]
    mgr.add(W("time", id=3, at=time.time() + 60, then="stretch"))
    mgr.add(W("file", id=4, path="C:/x", state={"mode": "appear", "_seen": [1, 2]}))
    saved = mgr.to_list()
    assert saved[1]["state"] == {"mode": "appear"}
    other = WatchManager(OWN_PID, platform=desk.platform)
    other._ensure_thread = lambda: None  # type: ignore[method-assign]
    assert other.restore(saved + [{"kind": "earthquake", "id": 9, "label": "x"}, {"nonsense": 1}]) == 2
    assert [(w.id, w.kind, w.then) for w in other.active()] == [(3, "time", "stretch"), (4, "file", "")]
    assert other.new_id() == 5
    assert other.restore([]) == 0


class Steps:
    """Stands in for the manager's stop event: lets its loop run ``n`` rounds."""

    def __init__(self, n: int) -> None:
        self.n = n

    def wait(self, _timeout: float) -> bool:
        self.n -= 1
        return self.n < 0

    def clear(self) -> None:
        pass

    def set(self) -> None:
        self.n = 0


def test_loop_fires_and_ends_when_nothing_is_left(mgr: WatchManager) -> None:
    fired: list = []
    mgr.fired.connect(lambda w, detail, timed_out: fired.append((w.id, detail, timed_out)))
    mgr.add(W("time", id=1, at=time.time() - 1))
    mgr.add(W("time", id=2, at=time.time() + 3600))
    mgr._stop = Steps(1)  # type: ignore[assignment]
    mgr._loop()
    assert [(i, re.sub(r"\d\d:\d\d", "HH:MM", d), t) for i, d, t in fired] == [(1, "It is HH:MM now.", False)]
    assert [w.id for w in mgr.active()] == [2]
    assert mgr.active()[0].state["_next"] > time.time()
    mgr.cancel(None)
    mgr._stop = Steps(5)  # type: ignore[assignment]
    mgr._loop()
    assert mgr._stop.n == 4                                       # stopped after one round: nothing to watch


def test_loop_gives_up_after_the_time_limit(mgr: WatchManager, tmp_path: Path) -> None:
    fired: list = []
    mgr.fired.connect(lambda w, detail, timed_out: fired.append((detail, timed_out)))
    mgr.add(W("file", id=1, path=str(tmp_path / "never.txt"), started=time.time() - 1800, until=time.time() - 1,
              state={"mode": "appear"}))
    mgr._stop = Steps(1)  # type: ignore[assignment]
    mgr._loop()
    assert [(re.sub(r"\d\d:\d\d", "HH:MM", d), t) for d, t in fired] == [
        ("It did NOT happen - I gave up after 30 minutes (at HH:MM).", True)]


def test_loop_survives_failing_checks(mgr: WatchManager, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fired: list = []
    mgr.fired.connect(lambda w, detail, timed_out: fired.append(detail))
    mgr.add(W("time", id=1, at=time.time() + 3600))

    def boom(w):  # type: ignore[no-untyped-def]
        raise RuntimeError("COM died")
    monkeypatch.setattr(mgr.checker, "check", boom)
    for _ in range(29):
        mgr.active()[0].state["_next"] = 0
        mgr._stop = Steps(1)  # type: ignore[assignment]
        mgr._loop()
    assert not fired and mgr._errors[1] == 29
    mgr.active()[0].state["_next"] = 0
    mgr._stop = Steps(1)  # type: ignore[assignment]
    mgr._loop()
    assert fired == ["I had to stop watching - checking kept failing (COM died)."] and not mgr.active()


def test_fire_only_once(mgr: WatchManager) -> None:
    fired: list = []
    mgr.fired.connect(lambda w, detail, timed_out: fired.append(w.id))
    w = W("time", id=1)
    mgr.add(w)
    mgr._fire(w, "x", False)
    mgr._fire(w, "x", False)
    assert fired == [1]


def test_thread_starts_and_stops(monkeypatch, desk) -> None:  # type: ignore[no-untyped-def]
    m = WatchManager(OWN_PID, platform=desk.platform)
    started: list = []
    monkeypatch.setattr(m, "_loop", lambda: started.append(1))
    m.add(W("time", id=1))
    m._thread.join(2)  # type: ignore[union-attr]
    assert started == [1]
    m.stop()
    assert m._stop.is_set()
