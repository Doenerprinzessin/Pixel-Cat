"""Apps and system settings the chat can open (plain names, no platform calls)."""
from __future__ import annotations

# the Settings pages the chat can open by name; the desktop adapter maps each one to its own address
SETTINGS_PAGES = (
    "home", "display", "night light", "sound", "bluetooth", "wifi", "network", "vpn", "notifications", "focus",
    "power", "battery", "storage", "apps", "default apps", "startup apps", "personalization", "background",
    "colors", "themes", "taskbar", "mouse", "keyboard", "printers", "date and time", "language", "privacy",
    "windows update", "about", "accessibility",
)
