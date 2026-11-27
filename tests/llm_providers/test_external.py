"""external.py: model tags, context windows, 'too long' errors, amounts, keys.dat, labels, token
and credit limits, and the helpers every external provider shares."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from pixelcat.llm import external as X

T0 = datetime(2026, 11, 19, 15, 0)          # a Thursday


# ------------------------------------------------------------------ tags and providers
@pytest.mark.parametrize("tag, expected", [
    ("anthropic:claude-opus-5-5", ("anthropic", "claude-opus-5-5")),
    ("openai:gpt-6.1", ("openai", "gpt-6.1")),
    ("copilot:claude-opus-5.5", ("copilot", "claude-opus-5.5")),
    ("copilot:a:b", ("copilot", "a:b")),
    ("qwen3.5:9b", None),
    ("anthropic:", None),
    ("anthropic", None),
    ("", None),
    (None, None),
])
def test_parse_tags(tag, expected):
    assert X.parse(tag) == expected


def test_providers_registry_has_the_three_external_providers():
    assert set(X.PROVIDERS) == {"anthropic", "openai", "copilot"}
    assert all(p.key == k for k, p in X.PROVIDERS.items())


# ------------------------------------------------------------------ context windows
@pytest.mark.parametrize("model, expected", [
    ("gpt-6.2", 922_000),
    ("gpt-6.2-sol-2026-06-01", 922_000),
    ("gpt-5.4-mini", 272_000),               # the longest matching name wins
    ("gpt-5.4", 922_000),
    ("gpt-5.1-chat-latest", 111_616),
    ("o3-mini", 100_000),
    ("o1-mini", 62_464),
    ("gpt-9-nova", None),
    ("xgpt-5", None),
])
def test_openai_input(model, expected):
    assert X.openai_input(model) == expected


@pytest.mark.parametrize("provider, model, info, expected", [
    ("anthropic", "claude-opus-5-5", {"context": 1_000_000, "max_tokens": 128_000}, 968_000),
    ("anthropic", "claude-old", {}, 168_000),
    ("anthropic", "claude-small", {"context": 200_000, "max_tokens": 8_000}, 192_000),
    ("anthropic", "claude-tiny", {"context": 10_000}, X.MIN_BUDGET),
    ("openai", "gpt-6.2-sol-2026-06-01", {}, 922_000),
    ("openai", "gpt-5.4-mini", {}, 272_000),
    ("openai", "gpt-5.1-chat-latest", {}, 111_616),
    ("openai", "o3-mini", {}, 100_000),
    ("openai", "gpt-6.1-sol", {"context": 1_050_000}, 922_000),
    ("openai", "gpt-9-nova", {}, X.OPENAI_UNKNOWN),
    ("openai", "gpt-9-nova", {"context": 272_000}, 204_000),
    ("openai", "gpt-9-nova", {"context": 1_000}, X.MIN_BUDGET),
    ("copilot", "anything", {}, X.OPENAI_UNKNOWN),
])
def test_max_input(provider, model, info, expected):
    assert X.max_input(provider, model, info) == expected


TOO_LONG = [
    ("prompt is too long: 250,000 tokens > 200,000 maximum", 200_000, 250_000),
    ("input length and `max_tokens` exceed context limit: 990000 + 32000 > 1000000, decrease input length",
     968_000, 990_000),
    ("This model's maximum context length is 128000 tokens. However, your messages resulted in 130000 tokens.",
     128_000, 130_000),
    ("Your input exceeds the context window of this model. Please adjust your input and try again.", None, None),
    ("context_length_exceeded", None, None),
]


@pytest.mark.parametrize("message, limit, actual", TOO_LONG)
def test_too_long_is_recognised_with_its_numbers(message, limit, actual):
    assert X.is_too_long(message)
    data = json.loads(X.too_long(message))
    assert (data["limit"], data["actual"]) == (limit, actual)
    assert data["detail"] == message[:200]


@pytest.mark.parametrize("message", ["slow down", "", "invalid x-api-key", "tokens per minute exceeded"])
def test_other_errors_are_not_too_long(message):
    assert not X.is_too_long(message)


def test_too_long_detail_is_clipped():
    assert len(json.loads(X.too_long("prompt is too long " + "x" * 500))["detail"]) == 200


# ------------------------------------------------------------------ amounts
@pytest.mark.parametrize("n, text", [
    (0, "0"), (750, "750"), (1000, "1k"), (1500, "1.5k"), (1700, "1.7k"), (9_999, "10k"), (12_000, "12k"),
    (999_499, "999k"), (999_999, "1M"), (1_234_567, "1.23M"), (3_500_000, "3.5M"), (100_000_000, "100M"),
])
def test_fmt_tokens(n, text):
    assert X.fmt_tokens(n) == text


@pytest.mark.parametrize("text, value", [
    ("3.5M", 3.5e6), ("750k", 750e3), ("2 000 000", 2e6), ("2'000'000", 2e6), ("2,000,000", 2e6),
    ("3,5M", 3.5e6), ("100 million", 1e8), ("1.234.567", 1_234_567.0), ("12.5k credits", 12_500.0),
    ("2\u00a0000", 2000.0), ("1,234.5", 1234.5), ("1.234,5", 1234.5), ("5 tokens", 5.0), ("3 Mio", 3e6),
    ("2 thousand", 2000.0), ("abc", None), ("-5", None), ("0", None), ("", None), ("inf", None), ("nan", None),
])
def test_parse_amount(text, value):
    assert X.parse_amount(text) == value


@pytest.mark.parametrize("x, text", [
    (0, "0"), (-3, "0"), (0.04, "0.04"), (0.5, "0.5"), (5, "5"), (28.1, "28.1"), (99.96, "100"),
    (5000, "5,000"), (20_000, "20,000"),
])
def test_fmt_credits(x, text):
    assert X.fmt_credits(x) == text


@pytest.mark.parametrize("at, text", [
    (datetime(2026, 11, 19, 18, 5), "today at 18:05 (in 3 h 5 min)"),
    (datetime(2026, 11, 19, 15, 0, 20), "today at 15:00 (in 1 min)"),
    (datetime(2026, 11, 20), "tomorrow at 00:00 (in 9 h 0 min)"),
    (datetime(2026, 11, 23), "Mon 23 Nov at 00:00 (in 3 d 9 h)"),
    (datetime(2026, 11, 19, 14, 0), "today at 14:00 (in 1 min)"),
])
def test_fmt_reset(at, text):
    assert X.fmt_reset(at, T0) == text


# ------------------------------------------------------------------ keys.dat
def test_credentials_are_kept_encrypted_and_read_back(tmp_path, fake_crypt):
    c = X.Credentials(tmp_path)
    c.set("anthropic_key", "sk-ant-secret-123")
    c.set("chatgpt", {"access_token": "AT"})
    raw = (tmp_path / "keys.dat").read_bytes()
    assert raw.startswith(X.Credentials.MAGIC)
    assert b"sk-ant-secret" not in raw
    again = X.Credentials(tmp_path)
    assert again.get("anthropic_key") == "sk-ant-secret-123"
    assert again.get("chatgpt") == {"access_token": "AT"}
    assert again.get("missing", "dflt") == "dflt"
    assert not (tmp_path / "keys.dat.tmp").exists()


def test_credentials_default_to_the_data_folder(fake_crypt):
    from pixelcat import paths

    X.Credentials().set("openai_key", "sk-1")
    assert (paths.data_dir() / "keys.dat").is_file()
    assert X.Credentials().get("openai_key") == "sk-1"


def test_credentials_set_none_forgets(tmp_path, fake_crypt):
    c = X.Credentials(tmp_path)
    c.set("a", 1)
    c.set("b", 2)
    c.set("a", None)
    assert X.Credentials(tmp_path).get("a") is None
    assert X.Credentials(tmp_path).get("b") == 2


@pytest.mark.parametrize("content", [
    b"",                                                       # no magic
    b"something else",
    X.Credentials.MAGIC + b"not-ours",                         # can't be decrypted (another account)
    X.Credentials.MAGIC + b"FAKE" + bytes(b ^ 0x5A for b in b"{broken json"),
    X.Credentials.MAGIC + b"FAKE" + bytes(b ^ 0x5A for b in b"[1, 2]"),
])
def test_unreadable_keys_file_reads_as_empty(tmp_path, fake_crypt, content):
    (tmp_path / "keys.dat").write_bytes(content)
    assert X.Credentials(tmp_path).get("anthropic_key") is None


def test_missing_keys_file_reads_as_empty(tmp_path, fake_crypt):
    assert X.Credentials(tmp_path).get("x") is None
    assert not (tmp_path / "keys.dat").exists()


def test_memory_credentials_are_never_written(fake_crypt):
    from pixelcat import paths

    mem = X.MemoryCredentials({"openai_key": "sk-try"})
    mem.set("x", 1)
    mem.set("openai_key", None)
    assert mem.get("x") == 1 and mem.get("openai_key") is None
    assert not (paths.data_dir() / "keys.dat").exists()


# ------------------------------------------------------------------ names and messages
def test_label_uses_the_providers_name_for_the_model():
    assert X.label("qwen3.5:9b") == "qwen3.5:9b"
    assert X.label("anthropic:claude-x") == "claude-x (Anthropic)"
    X.NAMES["openai:gpt-6.1"] = "GPT-6.1"
    assert X.label("openai:gpt-6.1") == "GPT-6.1 (OpenAI)"
    assert X.label("copilot:m") == "m (GitHub)"


@pytest.mark.parametrize("tag, name, short", [
    ("qwen3.5:9b", None, "QWEN3."),
    ("anthropic:claude-opus-5-5", "Claude Opus 5.5", "OPUS 5.5"),
    ("anthropic:claude-opus-5-5", None, "OPUS-5"),
    ("openai:gpt-6.1", None, "GPT-6.1"),
    ("openai:gpt-6.1-sol", "GPT-6.1 Sol", "GPT-6.1"),
    ("copilot:supercalifragilistic-2", None, "SUPERCALIF"),
    ("openai:o3", None, "O3"),
    ("openai:chatgpt_4o", None, "CHATGPT-4O"),
    ("openai:chatgpt-turbo", None, "CHATGPT"),
])
def test_short_label(tag, name, short):
    if name:
        X.NAMES[tag] = name
    assert X.short_label(tag) == short


@pytest.mark.parametrize("kind, needle", [
    ("auth", "didn't accept my sign-in (bad key)"),
    ("ext_offline", "I can't reach OpenAI"),
    ("timeout", "OpenAI took too long"),
    ("refused", "The model said no to this one (bad key)"),
    ("truncated", "cut off"),
    ("model_gone", "doesn't know the model **gpt-x**"),
    ("denied", "not allowed to do that: bad key"),
    ("busy", "very busy"),
    ("install", "Connect ChatGPT again"),
    ("too_long", "longer than **gpt-x**"),
    ("whatever", "Hiss... OpenAI said:\n```text\nbad key\n```"),
])
def test_friendly_messages(kind, needle):
    assert needle in X.friendly(kind, "bad key", "openai", "gpt-x")


def test_friendly_refused_without_detail_and_unknown_provider():
    assert "said no to this one. Try" in X.friendly("refused", "", "openai", "m")
    assert "Anthropic" in X.friendly("busy", "", "nobody", "m")


def test_friendly_too_long_uses_the_models_name():
    X.NAMES["anthropic:claude-x"] = "Claude X"
    assert "**Claude X**" in X.friendly("too_long", "", "anthropic", "claude-x")


# ------------------------------------------------------------------ periods
@pytest.mark.parametrize("period, start, reset", [
    ("day", datetime(2026, 11, 19), datetime(2026, 11, 20)),
    ("week", datetime(2026, 11, 16), datetime(2026, 11, 23)),
    ("month", datetime(2026, 11, 1), datetime(2026, 12, 1)),
    ("bogus", datetime(2026, 11, 19), datetime(2026, 11, 20)),
])
def test_period_start_and_next_reset(period, start, reset):
    assert X.period_start(period, T0) == start
    assert X.next_reset(period, T0) == reset


def test_next_reset_of_december_is_january():
    assert X.next_reset("month", datetime(2026, 12, 31, 23, 59)) == datetime(2027, 1, 1)


# ------------------------------------------------------------------ token limit
def test_budget_limit_raise_maximum_sequence():
    b = X.Budget({"limit": 1000, "period": "day", "maximum": 2.0}, {})
    b.add(999, T0)
    assert b.status(T0) == "ok"
    b.add(1, T0)
    assert b.status(T0) == "soft"
    assert b.next_allowed(T0) == 1500
    assert b.raise_limit(T0) == 1500
    assert b.status(T0) == "ok"
    b.add(500, T0)
    assert b.next_allowed(T0) == 2000             # capped at the maximum
    b.raise_limit(T0)
    b.add(500, T0)
    assert b.status(T0) == "hard"
    assert b.reset_at(T0) == datetime(2026, 11, 20)


def test_budget_new_period_starts_from_zero_without_raises():
    u: dict = {}
    b = X.Budget({"limit": 1000, "period": "day"}, u)
    b.add(1200, T0)
    b.raise_limit(T0)
    t1 = T0 + timedelta(days=1)
    assert (b.status(t1), b.used(t1), b.allowed(t1)) == ("ok", 0, 1000)
    assert u["start"] == datetime(2026, 11, 20).timestamp()


def test_budget_without_limit_or_maximum():
    assert X.Budget({"limit": 0}, {}).status(T0) == "ok"
    assert X.Budget({"limit": 0}, {}).allowed(T0) == 0
    free = X.Budget({"limit": 1000, "maximum": 0.0}, {})
    free.add(10**9, T0)
    assert free.status(T0) == "soft" and free.maximum == 0
    assert free.next_allowed(T0) == 10**9 + 500


def test_budget_defaults_and_bad_values():
    b = X.Budget({}, {})
    assert (b.limit, b.period, b.maximum) == (1_000_000, "day", 2_000_000)
    assert X.Budget({"period": "year"}, {}).period == "day"
    b.add(-50, T0)
    assert b.used(T0) == 0


def test_budget_week_and_month_periods():
    w = X.Budget({"limit": 10, "period": "week"}, {})
    w.add(5, datetime(2026, 11, 16, 9))
    assert w.used(datetime(2026, 11, 22, 23)) == 5
    assert w.used(datetime(2026, 11, 23, 0)) == 0
    m = X.Budget({"limit": 10, "period": "month"}, {})
    m.add(5, datetime(2026, 11, 2))
    assert m.used(datetime(2026, 11, 30)) == 5 and m.reset_at(T0) == datetime(2026, 12, 1)


# ------------------------------------------------------------------ credit limit
def test_credit_budget_sequence():
    cb = X.CreditBudget({"credit_limit": 1000}, {})
    cb.add(999.5, T0)
    assert cb.status(T0) == "ok"
    cb.add(0.5, T0)
    assert cb.status(T0) == "soft"
    assert cb.next_allowed(T0) == 1500.0
    assert cb.raise_limit(T0) == 1500.0
    assert cb.status(T0) == "ok"
    t1 = datetime(2026, 12, 1)
    assert (cb.used(t1), cb.allowed(t1)) == (0, 1000)
    assert cb.reset_at(T0) == t1


def test_credit_budget_default_and_no_limit():
    assert X.CreditBudget({}, {}).limit == X.CREDIT_DEFAULT
    nolimit = X.CreditBudget({"credit_limit": 0}, {})
    nolimit.add(1e9, T0)
    assert nolimit.status(T0) == "ok" and nolimit.allowed(T0) == 0.0
    cb = X.CreditBudget({"credit_limit": 1}, {})
    assert cb.next_allowed(T0) == 2.0                  # at least one credit more
    cb.add(-1, T0)
    assert cb.used(T0) == 0.0


# ------------------------------------------------------------------ shared helpers
def test_with_sources_adds_new_links_only():
    text = "See [A](https://a.example/x)."
    out = X.with_sources(text, [("A", "https://a.example/x"), ("B [beta]", "https://b.example/y"),
                                ("", "https://c.example/z"), ("dup", "https://b.example/y"),
                                ("ftp", "ftp://d.example/"), ("rel", "/local")])
    assert out == text + "\n\n**Sources:** [B (beta)](https://b.example/y) · [c.example](https://c.example/z)"


def test_with_sources_without_new_links_keeps_the_text():
    assert X.with_sources("plain  ", []) == "plain  "
    assert X.with_sources("x https://a.example", [("A", "https://a.example")]) == "x https://a.example"


def test_with_sources_limit():
    srcs = [(f"S{i}", f"https://s{i}.example/") for i in range(12)]
    assert X.with_sources("t", srcs).count("](https://") == 8
    assert X.with_sources("t", srcs, limit=2).count("](https://") == 2


def test_tool_call_ids_pairs_calls_and_results():
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "a"}},
                                                            {"id": "own", "function": {"name": "b"}}]},
        {"role": "tool", "content": "ra"},
        {"role": "tool", "content": "rb"},
        {"role": "tool", "content": "extra"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "c"}}]},
        {"role": "user", "content": "stop"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "d"}}]},
    ]
    out = X.tool_call_ids(msgs)
    assert [tc["_id"] for tc in out[1]["tool_calls"]] == ["call_1_0", "own"]
    assert [m.get("_call_id") for m in out[2:5]] == ["call_1_0", "own", None]
    assert out[0]["_unanswered"] == [] and out[6]["_unanswered"] == ["call_5_0"]
    assert out[-1] == {"role": "user", "content": "", "_unanswered": ["call_7_0"]}
    assert "_id" not in msgs[1]["tool_calls"][0]          # copies: the history isn't changed


@pytest.mark.parametrize("b64, kind", [
    ("iVBORw0KGgoAAA", "image/png"), ("R0lGODlh", "image/gif"), ("UklGRiQ", "image/webp"),
    ("/9j/4AAQSk", "image/jpeg"), ("", "image/jpeg"),
])
def test_media_type(b64, kind):
    assert X.media_type(b64) == kind


def test_wait_reset():
    assert X.wait_reset(None) is None and X.wait_reset(0) is None and X.wait_reset(-5) is None
    at = X.wait_reset(600)
    assert at is not None and 590 < (at - datetime.now()).total_seconds() <= 600


@pytest.mark.qt
def test_worker_limit_signal(record, creds):
    w = X.ExternalWorker("openai", "m", [], None, True, "high", creds)
    rec = record(w)
    w.limit("rate", datetime.fromtimestamp(1_800_000_000), "x" * 400)
    w.limit("credits")
    assert json.loads(rec.of("failed")[0][1]) == {"provider": "openai", "why": "rate", "detail": "x" * 300,
                                                  "reset": 1_800_000_000.0}
    assert json.loads(rec.of("failed")[1][1])["reset"] is None
    assert (w.raw, w.note, w.web, w._cancel) == (None, "", False, False)
    w.cancel()
    assert w._cancel
