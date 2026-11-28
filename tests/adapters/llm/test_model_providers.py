"""The AI model providers (pixelcat/adapters/llm): what each one answers without a request - how it is connected,
its effort levels, how much a request may hold, which errors speak for themselves, how it signs in and out, its
--selftest check - and that they stay light: a provider never used loads no SDK and stops nothing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pixelcat.adapters.llm import build_providers
from pixelcat.llm import external

ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = build_providers()
ALL = list(external.EFFORTS)


def test_the_providers_in_the_settings_order():
    assert list(PROVIDERS) == ["anthropic", "openai", "copilot", "ollama"]
    assert [p.info for p in PROVIDERS.values()][:3] == list(external.PROVIDERS.values())
    assert [p.info.local for p in PROVIDERS.values()] == [False, False, False, True]


@pytest.mark.parametrize(("p", "creds", "want", "auth"), [
    ("anthropic", {}, "", ""), ("anthropic", {"anthropic_key": "k"}, "", "key"),
    ("anthropic", {}, "signin", "signin"), ("anthropic", {"anthropic_key": "k"}, "signin", "signin"),
    ("openai", {}, "", ""), ("openai", {"openai_key": "k"}, "", "key"),
    ("openai", {"chatgpt": {"access_token": "a", "refresh_token": "r"}}, "chatgpt", "chatgpt"),
    ("openai", {"chatgpt": {"access_token": "a"}}, "chatgpt", ""),
    ("openai", {"openai_key": "k", "chatgpt": {}}, "chatgpt", "key"),
    ("copilot", {}, "", ""), ("copilot", {"copilot_key": "k"}, "", "key"),
    ("copilot", {"copilot_signin": {"token": "t"}}, "signin", "signin"),
    ("copilot", {"copilot_signin": {"token": ""}, "copilot_key": "k"}, "signin", "key"),
    ("copilot", {"copilot_signin": {"token": "t"}}, "", ""),
    ("ollama", {}, "", "local"),
])
def test_auth(p: str, creds: dict, want: str, auth: str) -> None:  # type: ignore[type-arg]
    assert PROVIDERS[p].auth(external.MemoryCredentials(creds), want) == auth


@pytest.mark.parametrize(("p", "info", "levels"), [
    ("anthropic", {"effort": ["low", "high"]}, ["low", "high"]), ("anthropic", {"effort": []}, None),
    ("anthropic", {"effort": ["max", "low"]}, ["low", "max"]), ("anthropic", {}, ALL),
    ("anthropic", {"efforts": ["low"]}, ALL),
    ("copilot", {"efforts": ["medium", "xhigh"]}, ["medium", "xhigh"]), ("copilot", {"efforts": []}, None),
    ("copilot", {"effort": ["low"]}, ALL), ("copilot", {}, ALL),
    ("openai", {"effort": ["low"], "efforts": ["low"]}, ALL),
    ("ollama", {}, ["low", "medium", "high"]),
])
def test_effort_levels(p: str, info: dict, levels) -> None:  # type: ignore[no-untyped-def,type-arg]
    assert PROVIDERS[p].effort_levels(info) == levels


@pytest.mark.parametrize(("p", "info"), [("anthropic", {"context": 200_000}), ("anthropic", {}),
                                         ("anthropic", {"context": 500_000, "max_tokens": 8000}),
                                         ("openai", {}), ("openai", {"context": 400_000})])
def test_max_input_is_the_models_own_maximum(p: str, info: dict) -> None:  # type: ignore[type-arg]
    assert PROVIDERS[p].max_input("gpt-5", info) == external.max_input(p, "gpt-5", info)


@pytest.mark.parametrize("p", ["copilot", "ollama"])
def test_max_input_isnt_tracked_where_the_context_setting_applies(p: str) -> None:
    assert PROVIDERS[p].max_input("m1", {"context": 200_000}) is None


@pytest.mark.parametrize(("p", "info", "refresh"), [
    ("anthropic", {"id": "m1"}, True), ("anthropic", {"id": "m1", "context": 200_000}, False),
    ("anthropic", {}, False), ("openai", {"id": "m1"}, False), ("copilot", {"id": "m1"}, False),
    ("ollama", {"id": "m1"}, False)])
def test_only_an_old_claude_list_is_loaded_again_for_the_context(p: str, info: dict, refresh: bool) -> None:  # type: ignore[type-arg]
    assert PROVIDERS[p].refresh_models_for_context(info) is refresh


@pytest.mark.parametrize("p", ["anthropic", "openai"])
def test_check_key_lists_the_models_with_only_that_key(monkeypatch, p: str) -> None:  # type: ignore[no-untyped-def]
    import importlib

    client = importlib.import_module(f"pixelcat.adapters.llm.{p}_client")
    seen: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(client, "list_models", lambda creds, auth: (seen.append((creds._data, auth)), ["m"])[1])
    assert PROVIDERS[p].check_key("sk-new") == ("", ["m"])
    assert seen == [({f"{p}_key": "sk-new"}, "key")]


def test_copilot_checks_a_token_itself(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import copilot_client

    monkeypatch.setattr(copilot_client, "check_token", lambda key: ("octocat", [{"id": key}]))
    assert PROVIDERS["copilot"].check_key("github_pat_x") == ("octocat", [{"id": "github_pat_x"}])


def test_the_local_models_take_no_key_and_have_no_quota() -> None:
    ollama = PROVIDERS["ollama"]
    with pytest.raises(ValueError):
        ollama.check_key("sk-x")
    assert ollama.list_models(None, "local") == [] and ollama.quota(None, "local") is None
    assert ollama.runtime_ready() and ollama.new_conversation() is None


def test_error_text_the_providers_own_errors_speak_for_themselves() -> None:
    from pixelcat.adapters.llm import ant_tool, chatgpt_signin, copilot_client, github_signin

    own = {"anthropic": [ant_tool.AntError("no ant")], "openai": [chatgpt_signin.SignInError("no chatgpt")],
           "copilot": [copilot_client.CopilotError("install", "no runtime"), github_signin.SignInError("no github")],
           "ollama": []}
    errors = [e for es in own.values() for e in es] + [RuntimeError("boom")]
    for p, prov in PROVIDERS.items():
        for exc in errors:
            assert prov.error_text(exc, "fallback") == (str(exc) if exc in own[p] else "fallback"), (p, exc)


def test_a_module_still_being_imported_counts_as_not_loaded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import types

    # (another thread's import puts the module in sys.modules before its names are there)
    for name in ("copilot_client", "github_signin", "ant_tool", "chatgpt_signin"):
        full = f"pixelcat.adapters.llm.{name}"
        monkeypatch.setitem(sys.modules, full, types.ModuleType(full))
    for prov in PROVIDERS.values():
        assert not prov.active()
        prov.shutdown(0.1)
        assert prov.error_text(RuntimeError("x"), "fallback") == "fallback"


def test_copilot_shutdown_stops_the_runtime_once_it_was_loaded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import copilot_client

    stopped: list[float] = []
    monkeypatch.setattr(copilot_client, "shutdown", stopped.append)
    for prov in PROVIDERS.values():
        prov.shutdown(4.0)
    assert stopped == [4.0]


def test_copilot_self_check_builds_a_client_it_never_starts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from copilot._cli_version import CLI_VERSION

    from pixelcat.adapters.llm import copilot_client

    made: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(copilot_client, "new_client", lambda token, path: made.append((token, path)))
    monkeypatch.setattr(copilot_client, "runtime_path", lambda: None)
    assert PROVIDERS["copilot"].self_check() == f"runtime {CLI_VERSION}, downloaded on first use"
    monkeypatch.setattr(copilot_client, "runtime_path", lambda: "rt")
    assert PROVIDERS["copilot"].self_check().endswith(", here")
    assert made == [("selftest", copilot_client.runtime_name())] * 2


def test_the_self_checks_say_what_they_found_or_raise(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import anthropic

    from pixelcat.adapters.llm import copilot_client

    checks = {p: prov.self_check() for p, prov in PROVIDERS.items()}
    assert checks["anthropic"] == f"anthropic {anthropic.__version__}" and checks["openai"] == checks["ollama"] == ""
    assert [p.info.build_part for p in PROVIDERS.values()] == ["external models"] * 2 + ["copilot sdk", ""]
    monkeypatch.setattr(copilot_client, "new_client", lambda token, path: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        PROVIDERS["copilot"].self_check()


@pytest.mark.parametrize(("p", "module", "calls"), [
    ("anthropic", "ant_tool", [("find", (), "find", ()), ("install", ("prog", "ev"), "install", ("prog", "ev")),
                               ("login", ("ant.exe", "open", "ev"), "login", ("ant.exe", "open", "ev"))]),
    ("openai", "chatgpt_signin", [("sign_in", ("creds", "ev", "again"), "sign_in", ("creds",)),
                                  ("start_over", ("creds",), "forget_unconfirmed", ("creds",)),
                                  ("sign_out", ("creds",), "sign_out", ("creds",))]),
    ("copilot", "github_signin", [("available", (), "available", ()),
                                  ("sign_in", ("creds", "show", "ev"), "sign_in", ("creds", "show", "ev")),
                                  ("sign_out", ("creds",), "sign_out", ("creds",))]),
])
def test_signin_calls_the_sign_in_module_when_it_runs(monkeypatch, p: str, module: str, calls: list) -> None:  # type: ignore[no-untyped-def,type-arg]
    import importlib

    mod = importlib.import_module(f"pixelcat.adapters.llm.{module}")
    signin = PROVIDERS[p].signin()
    assert PROVIDERS[p].info.signin_kind == {"anthropic": "cli", "openai": "browser", "copilot": "device"}[p]
    for method, args, func, want in calls:
        seen: list = []  # type: ignore[type-arg]
        monkeypatch.setattr(mod, func, lambda *a, _s=seen, **kw: (_s.append(a), "res")[1])
        res = getattr(signin, method)(*args)
        assert seen == [want] and res in ("res", None), (method, seen)


def test_signing_in_and_out_by_kind(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pixelcat.adapters.llm import ant_tool, chatgpt_signin

    gpt: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(chatgpt_signin, "sign_in", lambda creds, **kw: (gpt.append(kw), {"plan": True})[1])
    assert PROVIDERS["openai"].signin().sign_in("creds", "ev", "again") == {"plan": True}
    assert gpt == [{"cancel": "ev", "again": "again"}]
    out: list = []  # type: ignore[type-arg]
    monkeypatch.setattr(ant_tool, "find", lambda: "ant.exe")
    monkeypatch.setattr(ant_tool, "logout", out.append)
    PROVIDERS["anthropic"].signin().sign_out("creds")
    assert out == ["ant.exe"] and PROVIDERS["ollama"].signin() is None


_PROBE = """
import json, sys
from pixelcat.adapters.llm import build_providers
print("@@active " + json.dumps([prov.active() for prov in build_providers().values()]))
for prov in build_providers().values():
    prov.shutdown(0.1)
    prov.error_text(RuntimeError("x"), "y")
    prov.effort_levels({}), prov.max_input("m", {}), prov.refresh_models_for_context({})
heavy = ("anthropic", "copilot", "requests", "PySide6", "shiboken6", "pixelcat.adapters.llm.anthropic_client",
         "pixelcat.adapters.llm.openai_client", "pixelcat.adapters.llm.copilot_client", "pixelcat.adapters.llm.ollama_client")
print("@@" + json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in heavy or m in heavy)))
"""


def test_providers_are_light_and_one_never_used_stops_nothing() -> None:
    res = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    lines = [line for line in res.stdout.splitlines() if line.startswith("@@")]
    assert len(lines) == 2, res.stderr
    assert json.loads(lines[0].removeprefix("@@active ")) == [False] * 4
    assert json.loads(lines[-1][2:]) == []


def test_copilot_is_active_once_its_client_was_loaded() -> None:
    from pixelcat.adapters.llm import copilot_client  # noqa: F401

    assert [p.active() for p in PROVIDERS.values()] == [False, False, True, False]
