"""The tool catalogue the model sees: names, JSON schemas, confirm levels (a snapshot to catch drift)."""
from __future__ import annotations

import pytest

from pixelcat.domain import MEDIA_ACTIONS, SETTINGS_PAGES
from pixelcat.llm import tools as tools_mod
from pixelcat.llm.tools import CAT_ACTIONS, ToolBox, ToolError
from pixelcat.llm.watch import KINDS

# name: (required, {property: JSON type}, confirm, offered to the model, runs on a worker thread, ALWAYS group)
CATALOG = {
    "get_time": ([], {}, "none", True, False, ""),
    "system_info": ([], {}, "none", True, False, ""),
    "list_windows": ([], {}, "none", True, False, ""),
    "list_files": (["folder"], {"folder": "string"}, "none", True, False, ""),
    "get_volume": ([], {}, "none", True, False, ""),
    "set_timer": (["minutes"], {"minutes": "number", "label": "string"}, "none", True, False, ""),
    "set_reminder": (["time", "text"], {"time": "string", "text": "string"}, "none", True, False, ""),
    "cat_action": (["action"], {"action": "string"}, "none", True, False, ""),
    "open_app": (["name"], {"name": "string"}, "soft", True, True, ""),
    "window_action": (["target", "action"], {"target": "string", "action": "string"}, "soft", True, False, ""),
    "open_url": (["url"], {"url": "string"}, "soft", True, False, ""),
    "search_web": (["query"], {"query": "string"}, "soft", True, False, ""),
    "read_web_page": (["url"], {"url": "string", "start": "integer"}, "soft", True, True, "web"),
    "open_folder": (["folder"], {"folder": "string"}, "soft", True, False, ""),
    "open_settings": (["page"], {"page": "string"}, "soft", True, False, ""),
    "media_control": (["action"], {"action": "string"}, "soft", True, False, ""),
    "copy_to_clipboard": (["text"], {"text": "string"}, "soft", True, False, ""),
    "take_screenshot": ([], {}, "soft", True, False, ""),
    "set_volume": (["level"], {"level": "integer"}, "soft", True, False, ""),
    "mute": (["muted"], {"muted": "boolean"}, "soft", True, False, ""),
    "close_app": (["target"], {"target": "string"}, "hard", True, False, ""),
    "create_note": (["filename", "content"], {"folder": "string", "filename": "string", "content": "string"}, "hard",
                    True, False, ""),
    "lock_screen": ([], {}, "hard", True, False, ""),
    "look_at_screen": ([], {"target": "string"}, "none", True, True, ""),
    "read_window": ([], {"target": "string"}, "none", True, True, ""),
    "click": ([], {"element": "integer", "x": "number", "y": "number", "button": "string", "clicks": "integer",
                   "double": "boolean", "keys": "string", "hold": "number"}, "soft", True, True, "desktop"),
    "drag": ([], {"from_element": "integer", "from_x": "number", "from_y": "number", "to_element": "integer",
                  "to_x": "number", "to_y": "number", "path": "array", "button": "string", "keys": "string",
                  "seconds": "number"}, "soft", True, True, "desktop"),
    "move_mouse": ([], {"element": "integer", "x": "number", "y": "number", "seconds": "number"}, "soft", True, True,
                   "desktop"),
    "zoom": (["direction"], {"direction": "string", "steps": "integer", "element": "integer", "x": "number",
                             "y": "number", "how": "string", "target": "string"}, "soft", True, True, "desktop"),
    "type_text": (["text"], {"text": "string", "target": "string", "element": "integer"}, "soft", True, True,
                  "desktop"),
    "replace_text": (["old", "new"], {"old": "string", "new": "string", "target": "string",
                                      "replace_all": "boolean"}, "soft", True, True, "desktop"),
    "press_keys": (["keys"], {"keys": "string", "target": "string", "hold": "number"}, "soft", True, True, "desktop"),
    "scroll": (["direction"], {"direction": "string", "amount": "integer", "target": "string", "element": "integer",
                               "x": "number", "y": "number", "keys": "string"}, "soft", True, True, "desktop"),
    "watch_screen": ([], {"until": "string", "text": "string", "target": "string", "area": "array",
                          "sensitivity": "string", "seconds": "number"}, "none", True, True, ""),
    "wait": (["seconds"], {"seconds": "number"}, "none", True, True, ""),
    "wait_for": (["event", "then"], {"event": "string", "app": "string", "text": "string", "gone": "boolean",
                                     "path": "string", "at": "string", "minutes": "number", "then": "string"},
                 "none", True, True, ""),
    "stop_waiting": ([], {"id": "integer", "all": "boolean"}, "none", True, False, ""),
    "read_file": (["path"], {"path": "string"}, "none", True, True, ""),
    "edit_file": (["path", "old", "new"], {"path": "string", "old": "string", "new": "string",
                                           "replace_all": "boolean"}, "soft", True, True, "files"),
    "write_file": (["path", "content"], {"path": "string", "content": "string", "overwrite": "boolean"}, "soft",
                   True, True, "files"),
    "open_file": (["path"], {"path": "string", "app": "string"}, "soft", True, True, ""),
    "force_end": (["target"], {"target": "string"}, "hard", False, False, ""),
    "run_as_admin": (["name"], {"name": "string"}, "hard", False, False, ""),
}
EYES = {"look_at_screen", "read_window", "watch_screen", "wait_for", "read_file"}


def test_catalog_snapshot(toolbox: ToolBox) -> None:
    got = {n: (sp.required, {k: v.get("type") for k, v in sp.properties.items()}, sp.confirm, sp.llm, sp.background,
               sp.group) for n, sp in toolbox.specs.items()}
    assert got == CATALOG
    assert list(got) == list(CATALOG)


def test_names_and_schemas_only_offer_model_tools(toolbox: ToolBox) -> None:
    offered = [n for n, row in CATALOG.items() if row[3]]
    assert toolbox.names() == offered
    assert [s["function"]["name"] for s in toolbox.schemas()] == offered
    assert "force_end" not in toolbox.names() and "run_as_admin" not in toolbox.names()


def test_schema_shape(toolbox: ToolBox) -> None:
    for schema in toolbox.schemas():
        assert set(schema) == {"type", "function"} and schema["type"] == "function"
        fn = schema["function"]
        assert set(fn) == {"name", "description", "parameters"}
        assert fn["description"].strip()
        params = fn["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])
        assert all("type" in p for p in params["properties"].values())


@pytest.mark.parametrize(("tool", "prop", "values"), [
    ("cat_action", "action", CAT_ACTIONS),
    ("open_settings", "page", sorted(SETTINGS_PAGES)),
    ("media_control", "action", sorted(MEDIA_ACTIONS)),
    ("window_action", "action", ["focus", "minimize", "maximize", "restore"]),
    ("wait_for", "event", list(KINDS)),
    ("click", "button", ["left", "right", "middle", "back", "forward"]),
    ("drag", "button", ["left", "right", "middle"]),
    ("zoom", "direction", ["in", "out"]),
    ("zoom", "how", ["wheel", "pinch"]),
    ("scroll", "direction", ["up", "down", "left", "right"]),
    ("watch_screen", "until", ["change", "text", "gone", "still"]),
    ("watch_screen", "sensitivity", ["normal", "high", "low"]),
    ("open_file", "app", ["notepad", "vscode"]),
])
def test_enums(toolbox: ToolBox, tool: str, prop: str, values: list[str]) -> None:
    assert toolbox.specs[tool].properties[prop]["enum"] == values


def test_array_item_schemas(toolbox: ToolBox) -> None:
    assert toolbox.specs["drag"].properties["path"]["items"] == {"type": "array", "items": {"type": "number"}}
    assert toolbox.specs["watch_screen"].properties["area"]["items"] == {"type": "number"}


@pytest.mark.parametrize(("url", "eyes"), [
    ("http://localhost:11434", "none"), ("http://127.0.0.1:11434", "none"), ("http://[::1]:11434", "none"),
    ("http://0.0.0.0:11434", "none"), ("http://gpu-box.lan:11434", "soft"), ("https://ollama.example.com", "soft"),
])
def test_eyes_need_a_confirm_only_for_remote_models(cfg, hooks, url: str, eyes: str) -> None:
    cfg.ollama_url = url
    tb = ToolBox(cfg, hooks.hooks(), platform=hooks.platform)
    assert {n: tb.specs[n].confirm for n in EYES} == dict.fromkeys(EYES, eyes)


def test_open_app_description_lists_whitelist_and_start_menu(cfg, hooks) -> None:
    cfg.apps = {"zeta": ["z.exe"], "alpha": ["a.exe"]}
    desc = ToolBox(cfg, hooks.hooks(), platform=hooks.platform).specs["open_app"].description
    assert desc.startswith("Open an app by name: alpha, zeta or any app installed in the Start menu.")
    cfg.allow_start_menu_apps = False
    desc = ToolBox(cfg, hooks.hooks(), platform=hooks.platform).specs["open_app"].description
    assert desc.startswith("Open an app by name: alpha, zeta. ")


def test_toolbox_has_its_own_desktop_agent(toolbox: ToolBox) -> None:
    from llm_agent.agent_fakes import OWN_PID

    assert toolbox.agent.own_pid == OWN_PID and toolbox.agent.cfg is toolbox.cfg


@pytest.mark.parametrize("name", ["shell", "run_command", "", "Get_Time"])
def test_unknown_tools_are_refused(toolbox: ToolBox, name: str) -> None:
    with pytest.raises(ToolError, match=f"^'{name}' is not an allowed tool$"):
        toolbox.prepare(name, {})


@pytest.mark.parametrize("name", ["force_end", "run_as_admin"])
def test_force_only_tools_need_the_internal_flag(toolbox: ToolBox, name: str) -> None:
    with pytest.raises(ToolError, match="is not an allowed tool"):
        toolbox.prepare(name, {"target": "x", "name": "x"})


@pytest.mark.parametrize("args", [None, [], "text", 3])
def test_arguments_must_be_an_object(toolbox: ToolBox, args) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ToolError, match="^arguments must be an object$"):
        toolbox.prepare("get_time", args)


@pytest.mark.parametrize(("name", "args", "missing"), [
    ("set_timer", {}, "minutes"),
    ("set_reminder", {"time": "10:00"}, "text"),
    ("set_reminder", {"time": "", "text": "x"}, "time"),
    ("cat_action", {"action": None}, "action"),
    ("window_action", {"target": "x"}, "action"),
    ("open_url", {"url": ""}, "url"),
    ("create_note", {"filename": "a.txt"}, "content"),
    ("edit_file", {"path": "desktop/a.txt", "old": "a"}, "new"),
    ("replace_text", {"new": "x"}, "old"),
    ("wait_for", {"then": "x"}, "event"),
    ("press_keys", {}, "keys"),
])
def test_missing_required_arguments(toolbox: ToolBox, name: str, args: dict, missing: str) -> None:
    with pytest.raises(ToolError, match=f"^missing argument '{missing}'$"):
        toolbox.prepare(name, args)


def test_unknown_arguments_are_dropped(toolbox: ToolBox) -> None:
    spec, a = toolbox.prepare("set_timer", {"minutes": 5, "evil": "rm -rf", "_confirm": "none"})
    assert spec.name == "set_timer"
    assert a == {"minutes": 5.0, "label": "Timer"}


def test_describe_uses_the_spec(toolbox: ToolBox) -> None:
    spec, a = toolbox.prepare("set_timer", {"minutes": 5})
    assert toolbox.describe(spec, a) == "set a 5.0 minute timer"


def test_run_calls_the_tool_and_logs_without_contents(toolbox: ToolBox, hooks, caplog) -> None:  # type: ignore[no-untyped-def]
    spec, a = toolbox.prepare("copy_to_clipboard", {"text": "secret words"})
    with caplog.at_level("INFO", logger=tools_mod.__name__):
        assert toolbox.run(spec, a) == "Copied."
    assert hooks.calls == [("set_clipboard", "secret words")]
    assert "secret words" not in caplog.text and "<12 chars>" in caplog.text
