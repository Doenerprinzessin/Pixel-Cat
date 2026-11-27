"""anthropic_client.to_claude / tools_for_claude: the cat's history and tools as Claude's messages."""
from __future__ import annotations

import copy

import pytest

from llm_providers.fakes import HISTORY, JPEG, PNG, TOOLS
from pixelcat.llm.anthropic_client import to_claude, tools_for_claude


def _img(data: str, kind: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": kind, "data": data}}


def test_full_history_as_claude_messages():
    system, msgs = to_claude(HISTORY)
    assert system == "You are the cat."
    assert msgs == [
        {"role": "user", "content": [_img(PNG, "image/png"), {"type": "text", "text": "open notepad and look"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "On it."},
            {"type": "tool_use", "id": "call_2_0", "name": "open_app", "input": {"name": "notepad"}},
            {"type": "tool_use", "id": "call_2_1", "name": "take_screenshot", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_2_0", "content": [{"type": "text", "text": "opened Notepad"}],
             "is_error": False},
            {"type": "tool_result", "tool_use_id": "call_2_1",
             "content": [{"type": "text", "text": "here it is"}, _img(JPEG, "image/jpeg")], "is_error": False},
            {"type": "text", "text": "(note from the app)"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm", "signature": "S"},
            {"type": "text", "text": "Typing."},
            {"type": "tool_use", "id": "toolu_x", "name": "type_text", "input": {"text": "hi"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_x", "content": [{"type": "text", "text": "typed"}],
             "is_error": False}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_8_0", "name": "press_keys", "input": {"keys": "x"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_8_0", "content": "(not run - the user stopped it)",
             "is_error": True},
            {"type": "text", "text": "stop that, do something else"}]},
    ]


def test_history_is_not_changed():
    before = copy.deepcopy(HISTORY)
    to_claude(HISTORY)
    assert HISTORY == before


def test_strip_thinking_drops_replayed_thinking_only():
    _s, msgs = to_claude(HISTORY, strip_thinking=True)
    assert all(b["type"] not in ("thinking", "redacted_thinking") for m in msgs for b in m["content"])
    assert msgs[3]["content"][0] == {"type": "text", "text": "Typing."}


def test_roles_alternate_starting_with_user():
    _s, msgs = to_claude([{"role": "assistant", "content": "hello"}, {"role": "user", "content": "a"},
                          {"role": "user", "content": "b"}])
    assert msgs == [{"role": "user", "content": [{"type": "text", "text": "(continuing)"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
                    {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]


def test_second_system_message_becomes_user_text():
    system, msgs = to_claude([{"role": "system", "content": "one"}, {"role": "user", "content": "hi"},
                              {"role": "system", "content": "two"}])
    assert system == "one"
    assert msgs == [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "text", "text": "two"}]}]


def test_empty_messages_are_skipped_and_empty_assistant_says_done():
    _s, msgs = to_claude([{"role": "user", "content": "  "}, {"role": "user", "content": "x"},
                          {"role": "assistant", "content": ""}])
    assert msgs == [{"role": "user", "content": [{"type": "text", "text": "x"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "(done)"}]}]


def test_tool_results_error_flag_empty_text_and_bad_arguments():
    _s, msgs = to_claude([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "a", "arguments": "not a dict"}},
                                                            {"function": {"name": "b"}}]},
        {"role": "tool", "content": "error: no such app"},
        {"role": "tool", "content": ""},
    ])
    assert [b["input"] for b in msgs[1]["content"]] == [{}, {}]
    assert msgs[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1_0", "content": [{"type": "text", "text": "error: no such app"}],
         "is_error": True},
        {"type": "tool_result", "tool_use_id": "call_1_1", "content": [{"type": "text", "text": "(done)"}],
         "is_error": False}]


def test_result_without_a_call_is_plain_text():
    _s, msgs = to_claude([{"role": "user", "content": "hi"},
                          {"role": "tool", "tool_name": "look", "content": "seen", "images": [PNG, "", 5]}])
    assert msgs == [{"role": "user", "content": [
        {"type": "text", "text": "hi"}, {"type": "text", "text": "[result of look] seen"}, _img(PNG, "image/png")]}]


def test_unanswered_call_at_the_end_gets_a_not_run_result():
    _s, msgs = to_claude([{"role": "user", "content": "go"},
                          {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "a"}}]}])
    assert msgs[-1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "(not run - the user stopped it)", "is_error": True}]}


def test_raw_blocks_give_the_ids_and_drop_none_values():
    raw = [{"type": "text", "text": "Hi", "citations": None},
           {"type": "tool_use", "id": "toolu_real", "name": "a", "input": {"k": 1}}]
    _s, msgs = to_claude([{"role": "user", "content": "go"},
                          {"role": "assistant", "content": "Hi", "tool_calls": [{"id": "mine", "function": {"name": "a"}}],
                           "raw": raw},
                          {"role": "tool", "content": "ok"}])
    assert msgs[1]["content"] == [{"type": "text", "text": "Hi"},
                                  {"type": "tool_use", "id": "toolu_real", "name": "a", "input": {"k": 1}}]
    assert msgs[2]["content"][0]["tool_use_id"] == "toolu_real"


def test_raw_blocks_that_dont_match_the_calls_are_rebuilt():
    raw = [{"type": "thinking", "thinking": "t", "signature": "s"},
           {"type": "tool_use", "id": "toolu_1", "name": "a", "input": {}}]
    _s, msgs = to_claude([{"role": "user", "content": "go"},
                          {"role": "assistant", "content": "Doing", "raw": raw, "tool_calls": [
                              {"id": "c1", "function": {"name": "a"}}, {"id": "c2", "function": {"name": "b"}}]},
                          {"role": "tool", "content": "1"}, {"role": "tool", "content": "2"}])
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["c1", "c2"]


def test_raw_blocks_only_thinking_when_stripped_fall_back_to_text():
    _s, msgs = to_claude([{"role": "user", "content": "go"},
                          {"role": "assistant", "content": "answer",
                           "raw": [{"type": "redacted_thinking", "data": "x"}]}], strip_thinking=True)
    assert msgs[1]["content"] == [{"type": "text", "text": "answer"}]


def test_missing_results_are_filled_with_not_run():
    _s, msgs = to_claude([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "function": {"name": "a"}},
                                                            {"id": "b", "function": {"name": "b"}}]},
        {"role": "tool", "content": "only a"},
        {"role": "assistant", "content": "later"},
    ])
    assert [(b["tool_use_id"], b["is_error"]) for b in msgs[2]["content"]] == [("a", False), ("b", True)]
    assert msgs[2]["content"][1]["content"] == "(not run)"
    # current behaviour: the call left open is also answered once more after the last reply - a
    # result whose call isn't in the turn before it (Anthropic would refuse such a request)
    assert msgs[-1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "b", "content": "(not run - the user stopped it)", "is_error": True}]}


def test_tools_for_claude():
    assert tools_for_claude(TOOLS) == [
        {"name": "open_app", "description": "Open an app", "eager_input_streaming": True,
         "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
        {"name": "get_time", "description": "", "eager_input_streaming": True,
         "input_schema": {"type": "object", "properties": {}}}]
    assert "eager_input_streaming" not in tools_for_claude(TOOLS, eager=False)[0]
    assert tools_for_claude(None) == []


@pytest.mark.parametrize("bad", [[{"function": {"description": "no name"}}], [{}]])
def test_tools_without_a_name_raise(bad):
    with pytest.raises(KeyError):
        tools_for_claude(bad)
