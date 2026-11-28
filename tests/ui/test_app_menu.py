"""PixelCatApp: the right-click menu - a structural snapshot of its entries (top level, Settings,
AI model and the External AI models per provider and connection), and what each entry does.

The snapshot is what the refactor (provider branches out of app.py, app.py split into
controller / menus / provider settings / game loop) must keep exactly."""
from __future__ import annotations

from pathlib import Path

import pytest

from pixelcat import naming, paths, persistence, progress
from pixelcat.engine.states import S
from pixelcat.llm import external
from pixelcat.persistence import SavedState
from pixelcat.ui import theme
from pixelcat.ui.menu import MenuItem

pytestmark = pytest.mark.qt


def tree(items: list[MenuItem]) -> list:  # type: ignore[type-arg]
    """``"key | label [note]"`` per entry; an entry with children: ``(that, [children])``."""
    out: list = []  # type: ignore[type-arg]
    for it in items:
        s = f"{it.key} | {it.label}" + (f" [{it.note}]" if it.note else "")
        out.append((s, tree(it.children)) if it.children else s)
    return out


def find(items: list[MenuItem], key: str) -> MenuItem:
    for it in items:
        if it.key == key:
            return it
        if it.children:
            try:
                return find(it.children, key)
            except KeyError:
                pass
    raise KeyError(key)


def keys(items: list[MenuItem]) -> list[str]:
    return [it.key for it in items]


# ---------------------------------------------------------------- snapshot
def test_top_level(pet) -> None:  # type: ignore[no-untyped-def]
    items = pet._menu_items()
    assert [f"{it.key} | {it.label}" for it in items] == [
        "pause | Pause", "chat | Chat", "games | Minigames", "cosmetics | Cosmetics", "settings | Settings",
        "quit | Quit"]
    assert [it.separator_before for it in items] == [False, False, True, False, False, True]


def test_minigames_and_cosmetics(pet) -> None:  # type: ignore[no-untyped-def]
    items = pet._menu_items()
    assert tree(find(items, "games").children) == [
        "game:ball | Play ball [NEW!]", "game:laser | Laser pointer [NEW!]", "game:treats | Treat toss [NEW!]",
        "game:knock | Knock it off! [STAGE 3]", "game:mice | Mouse hunt [STAGE 5]", "game:fishing | Fishing [STAGE 8]",
        "game:hide | Hide and seek [STAGE 11]", "game:boxes | Box fort [STAGE 14]", "feed | Feed"]
    cos = find(items, "cosmetics").children
    assert [f"{c.key} | {c.label} [{c.note}]" for c in cos] == ["pass | Cat Pass [STAGE 1]", "skins | Skins []",
                                                                 "hats | Hats []"]
    assert len(cos[1].children) == len(progress.SKINS) and cos[2].children[0].key == "hat:"
    assert sum(len(g.children) for g in cos[2].children[1:]) == len(progress.HATS)


def test_settings(pet) -> None:  # type: ignore[no-untyped-def]
    settings = find(pet._menu_items(), "settings").children
    assert [f"{it.key} | {it.label}" for it in settings] == [
        "set_name | Name...", "set_style | Style", "set_accent | Accent color", "set_size | Size", "set_font | Font",
        "set_model | AI model", "progress | Your progress", "quiet | Quiet mode", "autostart | Start with Windows",
        "automode | Chat auto mode", "preload_ai | Load AI at start", "explore | Sit inside windows"]
    assert [it.checked for it in settings[7:]] == [False, False, False, True, False]
    assert keys(find(settings, "set_style").children) == [f"style:{k}" for k in theme.STYLES]
    assert keys(find(settings, "set_accent").children) == [f"accent:{k}" for k in theme.ACCENTS]
    assert keys(find(settings, "set_size").children) == ["size:small", "size:normal", "size:large", "size:huge"]
    assert tree(find(settings, "set_font").children) == ["font:default | Style's own", "font:pixel | Pixel",
                                                        "font:clean | Clean"]
    assert tree(find(settings, "progress").children) == ["export | Export progress...", "import | Import progress..."]


def test_ai_model(pet) -> None:  # type: ignore[no-untyped-def]
    model = find(pet._menu_items(), "set_model").children
    assert [x if isinstance(x, str) else x[0] for x in tree(model)] == [
        "model:qwen3.5:0.8b | Tiny 0.8B [1.2 GB]", "model:qwen3.5:2b | Small 2B [2.7 GB]",
        "model:qwen3.5:4b | Light 4B [3.3 GB]", "model:qwen3.5:9b | Medium 9B [6.6 GB]",
        "model:qwen3.5:27b | Large 27B [17 GB SLOW]", "model:qwen3.5:35b-a3b | Smart 35B [22 GB BEST]",
        "model:qwen3.5:122b-a10b | Huge 122B [81 GB TOO BIG]", "ext | External AI models"]
    assert [it.checked for it in model[:-1]].count(True) == 1 and find(model, "model:qwen3.5:35b-a3b").checked


def test_external_ai_models(pet) -> None:  # type: ignore[no-untyped-def]
    ext = pet._external_menu()
    assert (ext.key, ext.label, ext.note) == ("ext", "External AI models", "")
    assert [x if isinstance(x, str) else x[0] for x in tree(ext.children)] == [
        "ext_anthropic | Claude (Anthropic)", "ext_openai | ChatGPT (OpenAI)", "ext_copilot | Copilot (GitHub)",
        "ext:thinking | Thinking", "ext_effort | Effort", "ext:websearch | Web search [$10 PER 1K SEARCHES]"]
    assert tree(find(ext.children, "ext_effort").children) == [
        "ext:effort:low | Low", "ext:effort:medium | Medium", "ext:effort:high | High",
        "ext:effort:xhigh | Extra high", "ext:effort:max | Max"]


def test_external_is_marked_best_on_a_pc_without_room_for_a_local_ai(pet) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.domain import Hardware

    pet.platform.system.answers("hardware", lambda fresh=False: Hardware(ram_gb=8.0, cores=4, gpus=[],
                                                                         disk_free_gb=500.0))
    assert pet._external_menu().note == "BEST"


def connect(pet, p: str, auth: str) -> None:  # type: ignore[no-untyped-def]
    """Put a provider into a connection state the way the sign-in / key flows leave it."""
    s = pet._ext_p(p)
    if auth == "key":
        pet.creds.set(f"{p}_key", "secret")
        if p == "copilot":
            s["auth"], s["login"] = "key", "octocat"
    elif auth == "signin":
        s["auth"] = "signin"
        if p == "copilot":
            pet.creds.set("copilot_signin", {"token": "t"})
            s["login"] = "octocat"
    elif auth == "chatgpt":
        s["auth"] = "chatgpt"
        pet.creds.set("chatgpt", {"access_token": "a", "refresh_token": "r", "email": "cat@example.com"})
    if auth:
        s["models"] = [{"id": "m1", "name": "Model One"}, {"id": "m2", "name": "Model Two"}]


LIMITS = ("ext_limit_{p} | Token limit [1M/day]", "ext_period_{p} | Resets", "ext_max_{p} | Raise up to",
          "ext:used:{p} | Used 0 of 1M [TODAY]")
MODELS = ("ext_models_{p} | Model",)
CREDITS = ("ext_climit_copilot | Credit limit [5,000/MONTH]", "ext:cused:copilot | Used 0 of 5,000 credits [THIS MONTH]")

PROVIDER_SNAPSHOT = {
    ("anthropic", ""): ("ext_anthropic | Claude (Anthropic)", [
        "ext:state:anthropic | Not connected", "ext:signin:anthropic | Sign in with Anthropic",
        "ext:key:anthropic | Use an API key...", *LIMITS]),
    ("anthropic", "key"): ("ext_anthropic | Claude (Anthropic) [ON]", [
        "ext:state:anthropic | Connected with an API key", *MODELS, "ext:signin:anthropic | Sign in with Anthropic",
        "ext:key:anthropic | Change API key...", *LIMITS, "ext:out:anthropic | Remove API key"]),
    ("anthropic", "signin"): ("ext_anthropic | Claude (Anthropic) [ON]", [
        "ext:state:anthropic | Signed in with Anthropic", *MODELS, "ext:key:anthropic | Use an API key...", *LIMITS,
        "ext:out:anthropic | Sign out"]),
    ("openai", ""): ("ext_openai | ChatGPT (OpenAI)", [
        "ext:state:openai | Not connected", "ext:signin:openai | Sign in with ChatGPT",
        "ext:key:openai | Use an API key...", *LIMITS]),
    ("openai", "key"): ("ext_openai | ChatGPT (OpenAI) [ON]", [
        "ext:state:openai | Connected with an API key", *MODELS, "ext:signin:openai | Sign in with ChatGPT",
        "ext:key:openai | Change API key...", *LIMITS, "ext:out:openai | Remove API key"]),
    ("openai", "chatgpt"): ("ext_openai | ChatGPT (OpenAI) [ON]", [
        "ext:state:openai | Signed in with ChatGPT (cat@example.com)", *MODELS, "ext:key:openai | Use an API key...",
        *LIMITS, "ext:out:openai | Sign out"]),
    ("copilot", ""): ("ext_copilot | Copilot (GitHub)", [
        "ext:state:copilot | Not connected", "ext:key:copilot | Use a GitHub token...", *LIMITS, *CREDITS]),
    ("copilot", "key"): ("ext_copilot | Copilot (GitHub) [ON]", [
        "ext:state:copilot | Connected with a GitHub token (octocat)", *MODELS,
        "ext:key:copilot | Change GitHub token...", *LIMITS, *CREDITS, "ext:out:copilot | Remove token"]),
    ("copilot", "signin"): ("ext_copilot | Copilot (GitHub) [ON]", [
        "ext:state:copilot | Signed in with GitHub (octocat)", *MODELS, "ext:key:copilot | Use a GitHub token...",
        *LIMITS, *CREDITS, "ext:out:copilot | Sign out"]),
}


@pytest.mark.parametrize(("p", "auth"), list(PROVIDER_SNAPSHOT))
def test_provider_menu_per_connection(pet, p: str, auth: str) -> None:  # type: ignore[no-untyped-def]
    connect(pet, p, auth)
    item = pet._provider_menu(p)
    label, kids = PROVIDER_SNAPSHOT[(p, auth)]
    assert tree([item])[0][0] == label
    assert [x if isinstance(x, str) else x[0] for x in tree(item.children)] == [k.format(p=p) for k in kids]
    assert pet._ext_auth(p) == auth


@pytest.mark.parametrize("p", list(external.PROVIDERS))
def test_provider_sub_menus(pet, p: str) -> None:  # type: ignore[no-untyped-def]
    connect(pet, p, "key")
    kids = pet._provider_menu(p).children
    assert tree(find(kids, f"ext_models_{p}").children) == [
        f"model:{p}:m1 | Model One", f"model:{p}:m2 | Model Two", f"ext:refresh:{p} | Reload the list"]
    assert tree(find(kids, f"ext_limit_{p}").children) == [
        f"ext:limit:{p}:0 | No limit", f"ext:limit:{p}:50000 | 50k tokens", f"ext:limit:{p}:100000 | 100k tokens",
        f"ext:limit:{p}:250000 | 250k tokens", f"ext:limit:{p}:500000 | 500k tokens",
        f"ext:limit:{p}:1000000 | 1M tokens", f"ext:limit:{p}:2000000 | 2M tokens",
        f"ext:limit:{p}:5000000 | 5M tokens", f"ext:limit:{p}:10000000 | 10M tokens",
        f"ext:limit:{p}:25000000 | 25M tokens", f"ext:limit:{p}:50000000 | 50M tokens",
        f"ext:limit:{p}:100000000 | 100M tokens", f"ext:custom:{p} | Custom... [UP TO 100M]"]
    assert tree(find(kids, f"ext_period_{p}").children) == [
        f"ext:period:{p}:day | Every day", f"ext:period:{p}:week | Every week", f"ext:period:{p}:month | Every month"]
    assert tree(find(kids, f"ext_max_{p}").children) == [
        f"ext:max:{p}:1.5 | 1.5x (1.5M)", f"ext:max:{p}:2 | 2x (2M)", f"ext:max:{p}:3 | 3x (3M)",
        f"ext:max:{p}:5 | 5x (5M)", f"ext:max:{p}:0 | No maximum"]


def test_copilot_credit_limit_choices(pet) -> None:  # type: ignore[no-untyped-def]
    kids = pet._provider_menu("copilot").children
    assert tree(find(kids, "ext_climit_copilot").children) == [
        "ext:climit:copilot:0 | No limit", "ext:climit:copilot:500 | 500 credits",
        "ext:climit:copilot:1000 | 1,000 credits", "ext:climit:copilot:2000 | 2,000 credits",
        "ext:climit:copilot:5000 | 5,000 credits", "ext:climit:copilot:10000 | 10,000 credits",
        "ext:climit:copilot:20000 | 20,000 credits", "ext:ccustom:copilot | Custom..."]


def test_copilot_credits_with_a_known_plan(pet) -> None:  # type: ignore[no-untyped-def]
    connect(pet, "copilot", "key")
    pet._ext_p("copilot")["quota"] = {"total": 1500, "used": 300, "reset": "2026-12-15T12:00:00Z"}
    kids = pet._provider_menu("copilot").children
    assert tree(find(kids, "ext_climit_copilot").children) == [
        "ext:climit:copilot:0 | No limit", "ext:climit:copilot:500 | 500 credits",
        "ext:climit:copilot:1000 | 1,000 credits", "ext:climit:copilot:1500 | 1,500 credits (your whole plan)",
        "ext:ccustom:copilot | Custom: 5,000 credits [UP TO 1,500]"]
    quota = find(kids, "ext:quota:copilot")
    assert quota.label == "Copilot plan: 300 of 1,500 used" and quota.bar == pytest.approx(0.2)
    assert quota.note.startswith("RESETS ") and quota.note.endswith(" 15")     # (the month: the locale's)
    pet._ext_p("copilot")["quota"] = {"unlimited": True}
    assert "ext:quota:copilot | Copilot plan: no monthly limit" in tree(pet._provider_menu("copilot").children)


def test_copilot_sign_in_shows_when_this_build_can_do_it(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.llm import github_signin

    monkeypatch.setattr(github_signin, "CLIENT_ID", "test-client")
    assert "ext:signin:copilot | Sign in with GitHub" in tree(pet._provider_menu("copilot").children)


def test_no_limit_hides_resets_and_raise(pet) -> None:  # type: ignore[no-untyped-def]
    pet._set_token_limit("openai", 0)
    pet._on_ext_used("openai", 1234)
    kids = tree(pet._provider_menu("openai").children)
    assert "ext_limit_openai | Token limit [NONE]" in [x if isinstance(x, str) else x[0] for x in kids]
    assert "ext:used:openai | Used 1.2k today" in kids
    assert not any((x if isinstance(x, str) else x[0]).startswith(("ext_period", "ext_max")) for x in kids)


def test_chat_model_menu(pet) -> None:  # type: ignore[no-untyped-def]
    connect(pet, "anthropic", "key")
    items = pet._model_menu_items()
    assert [x if isinstance(x, str) else x[0] for x in tree(items)] == [
        "chatmodel:default | Default (Smart (35B))", "chatmodel:qwen3.5:0.8b | Tiny 0.8B [1.2 GB]",
        "chatmodel:qwen3.5:2b | Small 2B [2.7 GB]", "chatmodel:qwen3.5:4b | Light 4B [3.3 GB]",
        "chatmodel:qwen3.5:9b | Medium 9B [6.6 GB]", "chatmodel:qwen3.5:27b | Large 27B [17 GB SLOW]",
        "chatmodel:qwen3.5:35b-a3b | Smart 35B [22 GB BEST]", "chatmodel:qwen3.5:122b-a10b | Huge 122B [81 GB TOO BIG]",
        "chatmodels_anthropic | Claude models", "chateffort | Effort [DEFAULT]"]
    assert tree(find(items, "chateffort").children) == [
        "chateffort:default | Default (Medium)", "chateffort:low | Low [NO THINKING]",
        "chateffort:medium | Medium [THINKS WHEN PLANNING]", "chateffort:high | High [ALWAYS THINKS]"]


def test_a_downloaded_model_is_marked_ready(pet, tmp_path) -> None:  # type: ignore[no-untyped-def]
    manifest = tmp_path / "ollama-models" / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "9b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    assert find(pet._menu_items(), "model:qwen3.5:9b").note == "6.6 GB READY"


def test_game_notes_after_playing(make_app) -> None:  # type: ignore[no-untyped-def]
    st = SavedState(autostart_asked=True, xp=progress.xp_to_reach(3), plays=2, best_round=4,
                    game_plays={"laser": 1}, game_best={"laser": 12.0})
    games = find(make_app(st)._menu_items(), "games").children
    assert [g.note for g in games[:4]] == ["BEST 4", "BEST 12", "NEW!", "NEW!"]
    assert not games[3].locked


def test_the_menu_opens_next_to_the_cat(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet._open_menu()
    assert pet.menu.isVisible() and pet.menu.subtitle == pet.brain.state.value.replace("_", " ")
    assert keys(pet.menu.items)[0] == "pause"


def test_no_menu_while_the_cat_is_carried(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet.brain.pick_up()
    pet._open_menu()
    assert not pet.menu.isVisible() and pet.brain.state != S.HELD


# ---------------------------------------------------------------- what the entries do
def test_pause_and_resume(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("pause")
    assert pet.brain.paused and keys(pet._menu_items())[0] == "pause"
    assert pet._menu_items()[0].label == "Resume"
    pet._on_menu("pause")
    assert not pet.brain.paused


def test_quiet_mode(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("quiet")
    assert pet.quiet and pet.brain.state == S.LOAFING and app_env.saved_states[-1].quiet
    assert pet.tray._quiet.isChecked()
    pet._on_menu("quiet")
    assert not pet.quiet


def test_auto_mode(pet) -> None:  # type: ignore[no-untyped-def]
    pet._ensure_chat()
    pet._on_menu("automode")
    assert pet.saved.auto_mode and pet.chat.auto_mode and "Auto mode ON" in pet.chat.msgs[-1].text
    pet._on_menu("automode")
    assert not pet.saved.auto_mode and "Auto mode OFF" in pet.chat.msgs[-1].text


def test_start_with_windows(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("autostart")
    assert app_env.autostart_set == [True] and pet.saved.autostart_asked
    app_env.desktop.autostart = True
    assert find(pet._menu_items(), "autostart").checked
    pet._on_menu("autostart")
    assert app_env.autostart_set == [True, False]


def test_load_ai_at_start(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("preload_ai")
    assert pet.cfg.preload_on_start is False and app_env.user_settings == [("preload_on_start", False)]
    pet._on_menu("preload_ai")
    assert app_env.user_settings[-1] == ("preload_on_start", True) and app_env.ensures == [False]


def test_sit_inside_windows(pet, app_env) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("explore")
    assert pet.cfg.explore_windows and app_env.user_settings == [("explore_windows", True)]
    pet.ledges.wins[1] = object()
    pet._on_menu("explore")
    assert not pet.cfg.explore_windows and pet.ledges.wins == {}


def test_feed_and_find(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("feed")
    pet.brain.x, pet.brain.y = -5000, -5000
    pet._on_menu("find")
    assert (pet.brain.x, pet.brain.y) == (960, 1040 - 200) and pet.brain.bubble.kind == "bubble_exclaim"


@pytest.mark.parametrize(("key", "attr", "value"), [("style:midnight", "ui_style", "midnight"),
                                                    ("accent:teal", "ui_accent", "teal"),
                                                    ("size:large", "ui_size", "large"),
                                                    ("font:clean", "ui_font", "clean")])
def test_the_look(pet, app_env, key: str, attr: str, value: str) -> None:  # type: ignore[no-untyped-def]
    kind = key.split(":")[0]
    pet._ensure_chat()
    pet._on_menu(key)
    assert getattr(pet.saved, attr) == value and theme.choice(kind) == value and app_env.saved_states
    assert pet.chat.u == theme.unit_for_dpi(pet.chat.dpi)


def test_a_style_change_refreshes_the_open_menu(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet._open_menu()
    pet.menu.expanded = {"settings", "set_style"}
    pet._on_menu("style:mint")
    assert find(pet.menu.items, "style:mint").checked and pet.menu.isVisible()


def test_name(pet, app_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    shown: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(pet, "_show_amount_dialog", shown.append)
    pet._on_menu("set_name")
    dlg = shown[-1]
    dlg.field.setText("<b>Bad</b>")
    dlg._save()
    assert pet.saved.cat_name == "" and dlg.status.text()
    pet._ensure_chat()
    dlg.field.setText("  Mochi ")
    dlg._save()
    assert pet.saved.cat_name == "Mochi" and naming.custom() == "Mochi" and pet.menu.title == "MOCHI"
    assert pet._copilot_stale and pet.chat._title_text() == "MOCHI CHAT"
    pet.set_cat_name("")
    assert pet.menu.title == "PIXEL CAT"


def test_skins_and_hats(make_app) -> None:  # type: ignore[no-untyped-def]
    pet = make_app(SavedState(autostart_asked=True, xp=10**6))
    skin = list(progress.SKINS)[1]
    hat = next(iter(progress.HATS))
    pet._on_menu(f"skin:{skin}")
    pet._on_menu(f"hat:{hat}")
    assert (pet.saved.skin, pet.saved.hat, pet.sprites.skin.key, pet.overlay.hat) == (skin, hat, skin, hat)
    pet._on_menu("hat:")
    assert pet.saved.hat == ""
    pet._on_menu("skin:nonsense")
    assert pet.saved.skin == skin


def test_locked_skins_hats_and_games_do_nothing(pet) -> None:  # type: ignore[no-untyped-def]
    skin = next(k for k in progress.SKINS if not progress.is_unlocked(0, "skin", k))
    hat = next(k for k in progress.HATS if not progress.is_unlocked(0, "hat", k))
    pet._on_menu(f"skin:{skin}")
    pet._on_menu(f"hat:{hat}")
    pet._on_menu("game:boxes")
    assert (pet.saved.skin, pet.saved.hat, pet.mini) == ("charcoal", "", None)
    assert pet.brain.bubble.kind == "bubble_question"


def test_a_new_game_is_explained_first(pet) -> None:  # type: ignore[no-untyped-def]
    pet._on_menu("game:laser")
    pop = pet._popups[-1]
    assert pop.title == "Laser pointer" and pop.kind == "tutorial" and pet.mini is None
    pop.answered.emit(False)
    assert pet.mini is None and "laser" not in pet.saved.tutorials_seen


def test_a_minigame_starts_and_stops(pet) -> None:  # type: ignore[no-untyped-def]
    pet.start()
    pet._on_menu("game:laser")
    pet._popups[-1].ok.click()
    assert pet.saved.tutorials_seen == ["laser"] and pet._mini_active() and pet._running_game() == "laser"
    assert find(pet._menu_items(), "stopgame").label == "Stop Laser pointer"
    for _ in range(5):
        pet._last_tick -= 1 / 30
        pet._tick()
    pet._on_menu("stopgame")
    assert pet.mini is None and pet.saved.game_plays == {"laser": 1}


def test_ball_game_over_gives_xp_and_keeps_the_record(pet) -> None:  # type: ignore[no-untyped-def]
    pet._game_ended(3)
    assert (pet.saved.plays, pet.saved.best_round, pet.game.best, pet.saved.xp) == (1, 3, 3, 25 + 36)
    pop = pet._popups[-1]
    assert "caught the ball 3 times" in pop.text.text() and "+61 XP" in pop.text.text()
    pet._game_ended(5)
    assert pet.saved.best_round == 5 and "New record: 5 catches!" in pet._popups[-1].text.text()
    pet._game_ended(0)
    assert pet.saved.plays == 3


def test_an_unlock_offers_to_use_it(pet) -> None:  # type: ignore[no-untyped-def]
    pet.saved.xp = progress.xp_to_reach(2) - 1
    pet._award("ball", "Play ball", 50, "ok", "")
    pop = pet._popups[-1]
    assert progress.REWARDS[0] == ("hat", "party")
    assert "Stage 2! New hat: " in pop.text.text() and pop.ok.text() == "PUT IT ON" and pop.no.text() == "LATER"
    pop.ok.click()
    assert pet.saved.hat == "party" and pet.overlay.hat == "party"


def test_xp_per_play_is_capped(pet) -> None:  # type: ignore[no-untyped-def]
    pet._award("mice", "Mouse hunt", 10**6, "", "")
    assert pet.saved.xp == persistence.MAX_XP_PER_PLAY


def test_export_and_import_progress(make_app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    src = make_app(SavedState(autostart_asked=True, xp=1234, best_round=9, plays=10))
    target = tmp_path / "mine"
    monkeypatch.setattr(src, "_ask_file", lambda save, title, start: str(target))
    src._on_menu("export")
    exported = Path(str(target) + persistence.EXPORT_EXT)
    assert exported.is_file() and src._popups[-1].title == "Progress exported"

    dst = make_app()
    monkeypatch.setattr(dst, "_ask_file", lambda save, title, start: str(exported))
    dst._on_menu("import")
    ask = dst._popups[-1]
    assert ask.title == "Import progress?" and "1234 XP" in ask.text.text()
    ask.ok.click()
    assert (dst.saved.xp, dst.saved.best_round, dst.game.best) == (1234, 9, 9)
    assert any(p.title == "Progress loaded" for p in dst._popups)
    backups = list((paths.data_dir() / "progress-backups").glob("*"))
    assert len(backups) == 1


def test_import_of_a_broken_file_changes_nothing(pet, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.pixelcat"
    bad.write_bytes(b"not a cat")
    monkeypatch.setattr(pet, "_ask_file", lambda save, title, start: str(bad))
    pet._on_menu("import")
    assert pet._popups[-1].title == "Import" and pet.saved.xp == 0


def test_cancelled_file_dialogs_do_nothing(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pet, "_ask_file", lambda save, title, start: "")
    pet._on_menu("export")
    pet._on_menu("import")
    assert pet._popups == []


def test_a_failing_menu_action_is_logged_not_raised(pet, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom() -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(pet, "open_chat", boom)
    pet._on_menu("chat")


def test_the_tray_uses_the_same_actions(pet) -> None:  # type: ignore[no-untyped-def]
    pet.tray.action.emit("pause")
    assert pet.brain.paused
