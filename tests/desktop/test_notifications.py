"""pixelcat.desktop.notifications: the Windows notification database, read-only (temp SQLite DB)."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from pixelcat.desktop import notifications
from pixelcat.desktop.notifications import Feed, Notification, app_matches, badge_count, friendly

TEAMS = "MSTeams_8wekyb3d8bbwe!MSTeams"
OUTLOOK = "Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows"


@pytest.mark.parametrize("app_id, name", [
    (TEAMS, "Microsoft Teams"),
    ("com.squirrel.Teams.Teams", "Microsoft Teams"),
    (OUTLOOK, "Outlook"),
    ("microsoft.windowscommunicationsapps_8wekyb3d8bbwe!microsoft.windowslive.mail", "Mail"),
    ("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "WhatsApp"),
    ("MSEdge", "Edge"),
    (r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe", "PowerShell"),
    (r"C:\Tools\Backup\backuptool.exe", "backuptool"),
    ("Contoso.Notes_abc123!NotesApp", "NotesApp"),
    ("Contoso.Notes_abc123!App", "Notes"),
    ("Fabrikam.Weather_xyz!app", "Weather"),
])
def test_friendly(app_id, name):
    assert friendly(app_id) == name


@pytest.mark.parametrize("app_id, query, expected", [
    (TEAMS, "teams", True), (TEAMS, "Microsoft Teams", True), (TEAMS, "MS Teams", True),
    (OUTLOOK, "mail", True), (OUTLOOK, "e-mail", True), (OUTLOOK, "outlook", True), (TEAMS, "mail", False),
    ("MSEdge", "microsoft edge", True), ("Microsoft.Todos_8wekyb3d8bbwe!App", "to do", True),
    ("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "WhatsApp", True),
    ("Contoso.SuperChat_abc!App", "super chat", True),       # spaces ignored
    ("Contoso.SuperChat_abc!App", "Microsoft SuperChat", True),
    ("Contoso.SuperChat_abc!App", "teams", False),
    (TEAMS, "", True), (TEAMS, "  ", True),
])
def test_app_matches(app_id, query, expected):
    assert app_matches(app_id, query) is expected


@pytest.mark.parametrize("value, count", [("3", 3), (" 12 ", 12), ("newMessage", 1), ("alert", 1), ("0", 0),
                                          ("none", 0), ("None", 0), ("", 0), (None, 0)])
def test_badge_count(value, count):
    assert badge_count(value) == count


def test_notification_summary_and_contains():
    n = Notification(1, TEAMS, ["Anna", "Lunch at 12?"], datetime(2024, 5, 6, 11, 58))
    assert n.app == "Microsoft Teams"
    assert n.summary() == 'Microsoft Teams at 11:58: "Anna" - "Lunch at 12?"'
    assert Notification(2, "Contoso.Thing_1!App", [], datetime(2024, 1, 1, 9, 5)).summary() == "Thing at 09:05: (no text)"
    assert n.contains("lunch") and n.contains("TEAMS") and n.contains("") and not n.contains("dinner")


def filetime(dt: datetime) -> int:
    return int((dt - datetime(1601, 1, 1, tzinfo=timezone.utc)) / timedelta(microseconds=1)) * 10


def test_filetime_to_local_naive_datetime():
    utc = datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    assert notifications._filetime(filetime(utc)) == utc.astimezone().replace(tzinfo=None)


def test_filetime_missing_is_now(monkeypatch):
    before = datetime.now()
    got = notifications._filetime(None)
    assert before <= got <= datetime.now()


# ---------------------------------------------------------------- own notifications
@pytest.fixture
def own_texts(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(notifications, "_OWN_TEXTS", [])
    return notifications._OWN_TEXTS


def test_remember_own_normalises_and_expires(own_texts, monkeypatch):
    import time

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    notifications.remember_own("Pixel  Cat\nis   here", "", "   ", "x" * 200)
    assert own_texts == [(1000.0, "Pixel Cat is here"), (1000.0, "x" * 120)]
    assert notifications._is_own_text(["Pixel Cat", "is here"])
    assert not notifications._is_own_text(["Pixel Cat"])
    now[0] += 601
    notifications.remember_own("new")
    assert own_texts == [(1601.0, "new")]


def test_remember_own_keeps_the_last_twenty(own_texts):
    for i in range(30):
        notifications.remember_own(f"text {i}")
    notifications.remember_own("last")
    assert len(own_texts) == 21 and own_texts[0][1] == "text 10" and own_texts[-1][1] == "last"


def test_own_ids(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/App/PixelCat.exe")
    assert notifications._own_ids() == ("pixelcat", "pixelcat.exe")


# ---------------------------------------------------------------- Feed on a temp database
def toast(*texts: str, extra: str = "") -> str:
    body = "".join(f"<text>{t}</text>" for t in texts)
    return f'<toast launch="x"><visual><binding template="ToastGeneric">{body}{extra}</binding></visual></toast>'


@pytest.fixture
def db(tmp_path, own_texts, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sys, "executable", "C:/Python/python.exe")
    path = tmp_path / "wpndatabase.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);
        CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB,
                                   ArrivalTime INTEGER);
    """)
    handlers = {1: TEAMS, 2: OUTLOOK, 3: "PixelCat", 4: r"C:\Python\python.exe", 5: "Contoso.App_1!App"}
    con.executemany("INSERT INTO NotificationHandler VALUES (?, ?)", handlers.items())
    at = filetime(datetime(2024, 5, 6, 9, 30, tzinfo=timezone.utc))
    rows = [
        (1, 1, "toast", toast("Anna", "Lunch?").encode(), at),
        (2, 2, "toast", toast("New mail", "From: Bob &amp; Co", extra="<image src='x'/>"), at),
        (3, 3, "toast", toast("the cat's own").encode(), at),
        (4, 4, "toast", toast("python itself").encode(), at),
        (5, 1, "badge", b'<badge value="3"/>', at),
        (6, 2, "badge", '<badge version="1" value="newMessage"/>', at),
        (7, 5, "badge", b"<badge/>", at),
        (8, 5, "tile", b"<tile/>", at),
        (9, 5, "toast", toast("  spaced \n  out  ", "<b>bold</b> text", "", "c", "d", "e").encode(), None),
        (10, 5, "toast", toast("y" * 400).encode(), at),
        (11, 5, "toast", None, at),
    ]
    con.executemany("INSERT INTO Notification VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_feed_last_id_and_available(db, tmp_path):
    assert Feed(db).last_id() == 11 and Feed(db).available()
    assert not Feed(tmp_path / "missing.db").available()


def test_feed_since_reads_toasts_oldest_first_without_our_own(db):
    got = Feed(db).since(0)
    assert [(n.id, n.app_id) for n in got] == [(1, TEAMS), (2, OUTLOOK), (9, "Contoso.App_1!App"),
                                              (10, "Contoso.App_1!App"), (11, "Contoso.App_1!App")]
    assert got[0].texts == ["Anna", "Lunch?"]
    assert got[1].texts == ["New mail", "From: Bob & Co"]
    assert got[2].texts == ["spaced out", "bold text", "c", "d"]
    assert got[3].texts == ["y" * 300]
    assert got[4].texts == []
    arrived = datetime(2024, 5, 6, 9, 30, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert got[0].arrived == arrived


def test_feed_since_only_newer(db):
    assert [n.id for n in Feed(db).since(9)] == [10, 11]
    assert Feed(db).since(11) == []


def test_feed_skips_toasts_the_cat_showed(db):
    notifications.remember_own("New mail")
    assert [n.id for n in Feed(db).since(0)][:2] == [1, 9]


def test_feed_badges(db):
    assert Feed(db).badges() == {TEAMS: "3", OUTLOOK: "newMessage", "Contoso.App_1!App": ""}


def test_feed_never_writes(db):
    before = db.read_bytes()
    feed = Feed(db)
    feed.since(0)
    feed.badges()
    with pytest.raises(sqlite3.OperationalError):
        feed._query("DELETE FROM Notification")
    assert db.read_bytes() == before
