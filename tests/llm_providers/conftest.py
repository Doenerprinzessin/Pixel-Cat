"""The AI provider fixtures (fake_http, creds, fake_crypt, record, a fresh provider state for every test) for the
tests of the core's model code (pixelcat/llm).  They live with the providers' tests: tests/adapters/llm/conftest.py."""
from adapters.llm.conftest import creds, fake_crypt, fake_http, provider_state, record  # noqa: F401
