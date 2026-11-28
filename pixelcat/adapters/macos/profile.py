"""The cat's words and shortcuts on a Mac (``Platform.profile``): Command shortcuts, Finder, System Settings."""
from __future__ import annotations

from ...domain import WINDOWS, Profile

_OPEN = "/usr/bin/open"

# key names the user (or the model) may write -> the canonical names the ports know (domain.MODIFIERS)
KEY_ALIASES = (("command", "win"), ("cmd", "win"), ("meta", "win"), ("option", "alt"), ("opt", "alt"))

_normalize = Profile(key_aliases=KEY_ALIASES).normalize_combo

# shortcuts that close or end things, or lock the Mac: always a real confirmation (normalized, see Profile)
CLOSING_KEYS = {_normalize(k) for k in WINDOWS.closing_keys} | {
    "win+q", "win+w", "shift+win+w", "alt+win+w",       # quit the app, close the window / all windows
    "ctrl+win+q", "shift+win+q", "alt+shift+win+q",     # lock the screen, log out
    "win+delete", "shift+win+delete", "alt+shift+win+delete",   # move to the Bin, empty the Bin
}
# shortcuts that open a command line or system menus: ask even in auto mode
SYSTEM_KEYS = {_normalize(k) for k in WINDOWS.system_keys} | {
    "alt+win+esc",            # Force Quit Applications
    "win+space",              # Spotlight (starts anything by name)
    "ctrl+win+power", "ctrl+alt+win+power",   # restart, shut down
}

# open_app's table (name -> command): what the Windows config's apps are on a Mac
APPS = (
    ("notepad", (_OPEN, "-a", "TextEdit")),
    ("textedit", (_OPEN, "-a", "TextEdit")),
    ("wordpad", (_OPEN, "-a", "TextEdit")),
    ("calculator", (_OPEN, "-a", "Calculator")),
    ("paint", (_OPEN, "-a", "Preview")),
    ("preview", (_OPEN, "-a", "Preview")),
    ("explorer", (_OPEN, "-a", "Finder")),
    ("finder", (_OPEN, "-a", "Finder")),
    ("terminal", (_OPEN, "-a", "Terminal")),
    ("snipping tool", (_OPEN, "-a", "Screenshot")),
    ("screenshot", (_OPEN, "-a", "Screenshot")),
    ("settings", ("x-apple.systempreferences:",)),
    ("browser", ("https://www.bing.com",)),
    ("task manager", (_OPEN, "-a", "Activity Monitor")),
    ("activity monitor", (_OPEN, "-a", "Activity Monitor")),
)

# what users call an app -> its executable's name (lower case), for finding its windows (Teams' is "MSTeams")
WINDOW_ALIASES = (
    ("word", "microsoft word"), ("excel", "microsoft excel"), ("powerpoint", "microsoft powerpoint"),
    ("onenote", "microsoft onenote"), ("outlook", "microsoft outlook"), ("teams", "msteams"),
    ("microsoft teams", "msteams"), ("edge", "microsoft edge"),
    ("microsoft edge", "microsoft edge"), ("chrome", "google chrome"), ("google chrome", "google chrome"),
    ("firefox", "firefox"), ("safari", "safari"), ("vs code", "code"), ("vscode", "code"),
    ("visual studio code", "code"), ("file explorer", "finder"), ("explorer", "finder"), ("finder", "finder"),
    ("notepad", "textedit"), ("textedit", "textedit"), ("spotify", "spotify"), ("discord", "discord"),
    ("slack", "slack"), ("terminal", "terminal"), ("task manager", "activity monitor"),
    ("activity monitor", "activity monitor"), ("paint", "preview"), ("preview", "preview"),
    ("calculator", "calculator"), ("settings", "system settings"), ("system settings", "system settings"),
    ("mail", "mail"), ("whatsapp", "whatsapp"),
)

MAC = Profile(
    os="macOS",
    desktop="Mac desktop",
    admin_prompt="macOS asks for an administrator password",
    shell="zsh",
    prompt_note=("\nTHIS IS A MAC: shortcuts use cmd (cmd+c, cmd+v, cmd+s, cmd+n, cmd+t, cmd+l, cmd+z); in key "
                 "names 'cmd' (or 'win') = Command, 'alt' = Option. The file manager is Finder, the settings are "
                 "System Settings. macOS doesn't let apps read other apps' notifications: to wait for a message, "
                 "watch the app's window instead (wait_for event window_text)."),
    account="your Mac user account",
    other_account="another Mac user account or Mac",
    start_with="Open at Login",
    file_manager="Finder",
    settings="System Settings",
    lock_keys="Control+Command+Q",
    notification="macOS notification",
    app_menu="Applications folder",
    task_manager="Activity Monitor",
    text_editor="TextEdit",
    new_chat_keys="Cmd+N",
    setup_hint="Install it from ollama.com (the Mac app; open it once), then try again.",
    build_hint="",                     # no installer yet: MacInstaller says so
    held_hint="ctrl, shift, alt (option), win (cmd)",
    primary="cmd",
    doc_end="cmd+down",
    zoom="cmd",
    closing_keys=frozenset(CLOSING_KEYS),
    system_keys=frozenset(SYSTEM_KEYS),
    key_aliases=KEY_ALIASES,
    apps=APPS,
    window_aliases=WINDOW_ALIASES,
)
