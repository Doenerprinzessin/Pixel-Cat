"""Every adapter implements every port: each method and property, with the port's parameter names and defaults.
The chat ports (``ports.llm``) too: the model providers, the chat jobs, the Qt bridge's events sink, Copilot's
conversation and the three ways to sign in."""
from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from pixelcat import ports
from pixelcat.adapters import llm, null
from pixelcat.adapters.llm import signin
from pixelcat.adapters.windows import escwatch, notifications
from pixelcat.adapters.windows import platform as windows
from pixelcat.llm import anthropic_client, copilot_client, ollama_client, openai_client, qt_chat

IMPLEMENTATIONS = {
    ports.DisplayPort: (windows.WindowsDisplay, null.NullDisplay),
    ports.WindowPort: (windows.WindowsWindows, null.NullWindows),
    ports.InputPort: (windows.WindowsInput, null.NullInput),
    ports.EscapeWatcher: (escwatch.EscWatcher, null.NullEscapeWatcher),
    ports.ClipboardPort: (windows.WindowsClipboard, null.NullClipboard),
    ports.AccessibilityPort: (windows.WindowsAccessibility, null.NullAccessibility),
    ports.CapturePort: (windows.WindowsCapture, null.NullCapture),
    ports.SystemPort: (windows.WindowsSystem, null.NullSystem),
    ports.AppsPort: (windows.WindowsApps, null.NullApps),
    ports.ProcessPort: (windows.WindowsProcesses, null.NullProcesses),
    ports.NotificationPort: (windows.WindowsNotifications, null.NullNotifications),
    ports.NotificationFeed: (notifications.Feed, null.NullFeed),
    ports.SecretStore: (windows.WindowsSecretStore, null.NullSecretStore),
    ports.KnownFolders: (windows.WindowsKnownFolders, null.NullKnownFolders),
    ports.AutostartPort: (windows.WindowsAutostart, null.NullAutostart),
    ports.SaveMarks: (windows.WindowsSaveMarks, null.NullSaveMarks),
    ports.InstallerPort: (windows.WindowsInstaller, null.NullInstaller),
}
CHAT_IMPLEMENTATIONS = {
    ports.ModelProvider: (llm.AnthropicProvider, llm.OpenAIProvider, llm.CopilotProvider, llm.OllamaProvider),
    ports.ChatEvents: (qt_chat._QtEvents,),
    ports.ChatJob: (anthropic_client.AnthropicChat, openai_client.OpenAIChat, copilot_client.CopilotJob,
                    ollama_client.OllamaChat),
    ports.Conversation: (copilot_client.CopilotChat,),
    ports.CliSignIn: (signin.AntSignIn,),
    ports.BrowserSignIn: (signin.ChatGPTSignIn,),
    ports.DeviceSignIn: (signin.GitHubSignIn,),
}
CASES = [(port, impl) for port, impls in IMPLEMENTATIONS.items() for impl in impls]
CHAT_CASES = [(port, impl) for port, impls in CHAT_IMPLEMENTATIONS.items() for impl in impls]


def _members(cls: type) -> dict[str, object]:
    return {name: obj for name, obj in vars(cls).items() if not name.startswith("_")}


def _params(fn: object) -> list[tuple[str, object, object]]:
    return [(p.name, p.kind, p.default) for p in list(inspect.signature(fn).parameters.values())[1:]]  # type: ignore[arg-type]


def test_every_port_has_implementations():
    exported = {getattr(ports, name) for name in ports.__all__}
    protocols = {obj for obj in exported if isinstance(obj, type) and getattr(obj, "_is_protocol", False)}
    assert protocols == set(IMPLEMENTATIONS) | set(CHAT_IMPLEMENTATIONS)


@pytest.mark.parametrize("port, impl", CASES + CHAT_CASES,
                         ids=[f"{p.__name__}-{i.__qualname__}" for p, i in CASES + CHAT_CASES])
def test_implements_every_member(port, impl):  # type: ignore[no-untyped-def]
    for name, member in _members(port).items():
        got = inspect.getattr_static(impl, name, None)
        assert got is not None, f"{impl.__qualname__} lacks {name}"
        if isinstance(member, property):
            assert isinstance(got, property), f"{impl.__qualname__}.{name} must be a property"
        else:
            assert inspect.isfunction(got), f"{impl.__qualname__}.{name} must be a method"
            assert _params(got) == _params(member), f"{impl.__qualname__}.{name}{inspect.signature(got)}"


@pytest.mark.parametrize("port, impl", [c for c in CASES if c[1].__module__ != escwatch.__name__
                                        and c[1].__module__ != notifications.__name__],
                         ids=lambda x: getattr(x, "__qualname__", ""))
def test_adds_no_public_methods(port, impl):  # type: ignore[no-untyped-def]
    assert set(_members(impl)) <= set(_members(port))


@pytest.mark.parametrize("build", [windows.build, null.build], ids=["windows", "null"])
def test_build_fills_each_port_with_its_implementation(build):  # type: ignore[no-untyped-def]
    platform = build()
    hints = {f.name: f.type for f in fields(ports.Platform)}
    index = 0 if build is windows.build else 1
    for name, hint in hints.items():
        if name != "name":
            assert type(getattr(platform, name)) is IMPLEMENTATIONS[getattr(ports, hint)][index], name


def test_build_providers_makes_one_of_each_with_its_description():
    providers = llm.build_providers()
    assert [type(p) for p in providers.values()] == list(CHAT_IMPLEMENTATIONS[ports.ModelProvider])
    for key, prov in providers.items():
        assert isinstance(prov.info, ports.ProviderInfo) and prov.info.key == key
    assert set(ports.ModelProvider.__annotations__) == {"info"}      # (its only attribute; methods: above)
