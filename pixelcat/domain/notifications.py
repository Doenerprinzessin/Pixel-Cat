"""Notifications other apps showed, and the names the user calls those apps (plain data, no platform calls)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# (part of the app id, friendly name) - first match wins
_FRIENDLY = [("msteams", "Microsoft Teams"), ("teams", "Microsoft Teams"), ("outlook", "Outlook"),
             ("windowslive.mail", "Mail"), ("whatsapp", "WhatsApp"), ("discord", "Discord"), ("slack", "Slack"),
             ("telegram", "Telegram"), ("signal", "Signal"), ("zoom", "Zoom"), ("thunderbird", "Thunderbird"),
             ("msedge", "Edge"), ("chrome", "Chrome"), ("firefox", "Firefox"), ("todos", "Microsoft To Do"),
             ("powershell", "PowerShell"), ("securitycenter", "Windows Security"), ("onedrive", "OneDrive")]
_MAIL = ["outlook", "windowslive.mail", "thunderbird", "mail"]
# what the user may call an app -> parts of app ids that count as that app
_ALIASES = {"mail": _MAIL, "email": _MAIL, "e-mail": _MAIL, "emails": _MAIL, "mails": _MAIL,
            "teams": ["msteams", "teams"], "ms teams": ["msteams", "teams"], "microsoft teams": ["msteams", "teams"],
            "outlook": ["outlook"], "edge": ["msedge"], "microsoft edge": ["msedge"], "to do": ["todos"],
            "todo": ["todos"], "whatsapp": ["whatsapp"]}


def friendly(app_id: str) -> str:
    low = app_id.lower()
    for part, name in _FRIENDLY:
        if part in low:
            return name
    tail = re.split(r"[\\/!]", app_id)[-1]          # ...\powershell.exe, Package!App
    tail = re.sub(r"\.exe$", "", tail, flags=re.I)
    return tail if tail.lower() not in ("app", "") else app_id.split("_")[0].split(".")[-1]


def app_matches(app_id: str, query: str) -> bool:
    """Does a notification's app id belong to the app the user named ('teams', 'Outlook', 'mail')?"""
    q = query.strip().lower()
    if not q:
        return True
    needles = _ALIASES.get(q) or _ALIASES.get(re.sub(r"^(microsoft|ms)\s+", "", q)) or \
        [re.sub(r"^(microsoft|ms)\s+", "", q).replace(" ", "")]
    low = app_id.lower().replace(" ", "")
    return any(n in low for n in needles) or q in friendly(app_id).lower()


@dataclass
class Notification:
    id: int
    app_id: str
    texts: list[str] = field(default_factory=list)
    arrived: datetime = field(default_factory=datetime.now)

    @property
    def app(self) -> str:
        return friendly(self.app_id)

    def summary(self) -> str:
        body = " - ".join(f'"{t}"' for t in self.texts) or "(no text)"
        return f"{self.app} at {self.arrived:%H:%M}: {body}"

    def contains(self, text: str) -> bool:
        t = text.strip().lower()
        return not t or t in " ".join(self.texts).lower() or t in self.app.lower()


def badge_count(value: str | None) -> int:
    """'3' -> 3, a glyph like 'newMessage' -> 1, '0'/'none'/'' -> 0."""
    v = (value or "").strip().lower()
    if v.isdigit():
        return int(v)
    return 0 if v in ("", "none", "0") else 1
