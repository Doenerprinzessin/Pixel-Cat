"""The chat's own commands without the app: a recording chat window and toolbox, fake watches and model choice."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pixelcat.app.commands import ChatCommands
from pixelcat.app.state import ChatState
from pixelcat.llm.tools import ToolError
from pixelcat.persistence import SavedState
from pixelcat.settings import Config


class FakeChat:
    """The chat window's calls, recorded: ``calls`` [(name, *args)]; ``ask_confirm``'s answer callback kept."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.confirm: Any = None

    def ask_confirm(self, text: str, answered: Any) -> None:
        self.calls.append(("ask_confirm", text))
        self.confirm = answered

    def __getattr__(self, name: str) -> Any:
        return lambda *a: self.calls.append((name, *a))


class FakeToolBox:
    def __init__(self, denied: list[int] | None = None) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.denied = denied or []

    def prepare(self, tool: str, args: dict[str, Any], internal: bool = False) -> tuple[Any, dict[str, Any]]:
        self.calls.append(("prepare", tool, args, internal))
        if args.get("target") == "nothing":
            raise ToolError("no app called nothing is running")
        return SimpleNamespace(name=tool), {**args, "_denied": self.denied, "_plan": SimpleNamespace(label="Teams")}

    def describe(self, spec: Any, args: dict[str, Any]) -> str:
        return f"{spec.name} {args.get('target') or args.get('name')}"

    def run(self, spec: Any, args: dict[str, Any]) -> str:
        self.calls.append(("run", spec.name))
        return f"{spec.name} done"

    def force_end_admin(self, pids: list[int]) -> str:
        self.calls.append(("admin", pids))
        return "ended as admin"


def make(denied: list[int] | None = None) -> tuple[ChatCommands, SimpleNamespace]:
    f = SimpleNamespace(saves=0, chat=FakeChat(), toolbox=FakeToolBox(denied), saved=SavedState(), cancelled=[],
                        efforts=[])
    session = SimpleNamespace(toolbox=f.toolbox)
    watches = SimpleNamespace(active=lambda: [], cancel=lambda wid: (f.cancelled.append(wid),
                                                                     [SimpleNamespace(short=lambda: "the kettle")])[1])
    model_choice = SimpleNamespace(effort_levels=lambda: ["low", "medium", "high"], default_effort=lambda: "medium",
                                   effective_model=lambda: "qwen3.5:9b", set_chat_effort=f.efforts.append)
    cmds = ChatCommands(ChatState(), Config(), f.saved, SimpleNamespace(show_bubble=lambda *a: None), watches,  # type: ignore[arg-type]
                        SimpleNamespace(), session, SimpleNamespace(chat=f.chat), model_choice,  # type: ignore[arg-type]
                        save=lambda: setattr(f, "saves", f.saves + 1))
    return cmds, f


def info(f: SimpleNamespace) -> list[str]:
    return [c[2] for c in f.chat.calls if c[0] == "add"]


def test_force_end_runs_at_once_in_auto_mode_and_asks_before_using_admin_rights() -> None:
    cmds, f = make(denied=[41, 42])
    f.saved.auto_mode = True
    assert cmds.local_command("/force end the app Teams") is True
    assert f.toolbox.calls == [("prepare", "force_end", {"target": "Teams"}, True), ("run", "force_end")]
    assert info(f) == ["force_end done (auto)"] and "2 process(es) of Teams" in f.chat.calls[-1][1]
    f.chat.confirm("yes")
    assert f.toolbox.calls[-1] == ("admin", [41, 42]) and info(f)[-1] == "ended as admin"


def test_force_open_always_asks_and_a_tool_error_or_no_target_is_told() -> None:
    cmds, f = make()
    f.saved.auto_mode = True
    cmds.local_command("/force open notepad as admin")
    assert f.chat.calls == [("ask_confirm", "run_as_admin notepad?")] and len(f.toolbox.calls) == 1
    f.chat.confirm("no")
    assert info(f) == ["Okay, I won't."]
    cmds.local_command("/force end nothing")
    cmds.local_command("/force")
    assert f.chat.calls[-2] == ("add", "error", "🙀 no app called nothing is running")
    assert info(f)[-1].startswith("Usage: /force end <app>")


def test_the_other_commands_and_plain_messages() -> None:
    cmds, f = make()
    assert cmds.local_command("tell me a joke") is False and f.chat.calls == []
    f.saved.auto_approved_tools.extend(["files", "apps"])
    assert cmds.local_command("/permissions reset") and f.saved.auto_approved_tools == [] and f.saves == 1
    assert cmds.local_command("/unwatch 7") and f.cancelled == [7] and info(f)[-1] == "Stopped watching for: the kettle"
    assert cmds.local_command("/effort High") and f.efforts == ["high"]
    assert cmds.local_command("/effort extreme") and f.efforts == ["high"] and "This chat's effort" in info(f)[-1]


def test_the_commands_name_the_platforms_apps() -> None:
    from pixelcat.adapters.macos.profile import MAC

    def texts(profile: Any = None) -> str:
        cmds, f = make(denied=[41])
        if profile is not None:
            cmds.profile = profile
        f.saved.auto_mode = True
        for text in ("/force", "/help", "/force end teams"):
            cmds.local_command(text)
        return "\n".join(str(c[-1]) for c in f.chat.calls)

    win = texts()
    assert "Usage: /force end <app> (like Task Manager's End task, e.g. /force end teams) or /force open <app>" in win
    assert "/force end <app> (like Task Manager), /force open" in win and "e.g. 'open Notepad and write a" in win
    assert "Use admin rights to end them (Windows will ask you too)?" in win
    mac = texts(MAC)
    assert "(like Activity Monitor's End task" in mac and "(like Activity Monitor), /force open" in mac
    assert "e.g. 'open TextEdit and write a" in mac and "(macOS will ask you too)?" in mac
