"""pixelcat.domain.notifications: friendly app names, app matching, badges and Notification (plain data)."""
from __future__ import annotations

from datetime import datetime

import pytest

from pixelcat.domain import Notification, app_matches, badge_count, friendly

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
