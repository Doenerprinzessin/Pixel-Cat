"""Fixtures for the AI provider tests (pixelcat/llm).

* ``fake_http``: a scripted HTTP server on 127.0.0.1 that plays the providers' APIs (JSON,
  server-sent events, NDJSON) and records every request.  Routes are ``(method, path)``;
  each route holds a list of responses - one is used per request, the last one repeats.
* ``creds``: in-memory credentials (never written, never encrypted).
* ``fake_crypt``: a reversible fake secret store (pass it as ``secrets=``), so keys.dat
  tests never reach Windows' DPAPI (they run the same on Windows CI).
* ``record``: a Recorder of every event a provider's chat job reports, in order.
* Module globals the providers keep between requests (learned settings, model names, test
  endpoints) are reset for every test.
"""
from __future__ import annotations

import pytest

from llm_providers.fakes import FakeHTTP, Recorder


@pytest.fixture
def fake_http():
    srv = FakeHTTP()
    yield srv
    srv.close()


# ------------------------------------------------------------------ credentials
@pytest.fixture
def creds():
    from pixelcat.llm.external import MemoryCredentials

    return MemoryCredentials({})


class FakeCrypt:
    """A SecretStore that flips the bytes (never DPAPI)."""

    def protect(self, data: bytes) -> bytes:
        return b"FAKE" + bytes(b ^ 0x5A for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        from pixelcat import savecrypt

        if not blob.startswith(b"FAKE"):
            raise savecrypt.Unreadable("not ours")
        return bytes(b ^ 0x5A for b in blob[4:])


@pytest.fixture
def fake_crypt() -> FakeCrypt:
    return FakeCrypt()


# ------------------------------------------------------------------ provider state
@pytest.fixture(autouse=True)
def provider_state(monkeypatch: pytest.MonkeyPatch):
    """Fresh module state: nothing learned, no model names, the real endpoints, no env keys."""
    from pixelcat.llm import anthropic_client, external, openai_client

    monkeypatch.setattr(anthropic_client, "_learned", {})
    monkeypatch.setattr(openai_client, "_learned", {})
    monkeypatch.setattr(external, "NAMES", {})
    monkeypatch.setitem(external.BASE_URLS, "anthropic", None)
    monkeypatch.setitem(external.BASE_URLS, "openai", "https://api.openai.com/v1")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE",
                "ANTHROPIC_CONFIG_DIR", "OPENAI_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def record():
    """``rec = record(job)``: a Recorder of what the job (built with a RecordingEvents) reports."""
    return Recorder
