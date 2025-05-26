import re
from pathlib import Path

import pytest

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import HookError

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


def extract_examples(path: str) -> list:
    with open(DOCS_DIR / path) as fd:
        markdown = fd.read()
    pattern = re.compile(r"```toml(.*?)```", re.DOTALL)
    return pattern.findall(markdown)


ALL_CONFIGS = extract_examples("reference/configuration.md") + extract_examples("configuration.md")
DEFAULT_CONFIG = SchemathesisConfig()


def normalize_test_name(name: str, max_length: int = 30) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name[:max_length].strip("_")


@pytest.mark.parametrize("config", ALL_CONFIGS, ids=[normalize_test_name(i) for i in ALL_CONFIGS])
def test_configs(monkeypatch, config, snapshot_config):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    monkeypatch.setenv("TEST_STRING_2", "bar")
    monkeypatch.setenv("API_HOST", "http://example.schemathesis.io")
    monkeypatch.setenv("API_TOKEN", "secret")
    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setenv("USERNAME", "user-1")
    monkeypatch.setenv("PASSWORD", "secret-password")
    monkeypatch.setenv("CLIENT_ID", "admin")
    monkeypatch.setenv("CLIENT_SECRET", "secret!")
    monkeypatch.setenv("TOKEN", "secret-token!")
    monkeypatch.setenv("SESSION_ID", "secret-session-id!")
    monkeypatch.setenv("USER_ID", "42")
    monkeypatch.setenv("IDEMPOTENCY_KEY", "key!")
    try:
        config = SchemathesisConfig.from_str(config)
        assert config == snapshot_config
    except HookError as exc:
        assert str(exc) == snapshot_config
