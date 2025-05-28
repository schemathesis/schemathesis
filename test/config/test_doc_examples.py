import re
from pathlib import Path

import pytest
import yaml

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import HookError

ROOT_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = ROOT_DIR / "docs"
README_FILE = ROOT_DIR / "README.md"


def extract_examples(path: str, format: str = "toml") -> list:
    with open(DOCS_DIR / path, encoding="utf-8") as fd:
        markdown = fd.read()
    pattern = re.compile(rf"```{format}(.*?)```", re.DOTALL)
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


def collect_all_yaml_snippets() -> list[tuple[str, str]]:
    snippets = []

    for block in extract_examples(str(README_FILE), format="yaml"):
        snippets.append((str(README_FILE), block))

    for md_file in DOCS_DIR.rglob("*.md"):
        for block in extract_examples(str(md_file), format="yaml"):
            snippets.append((str(md_file.relative_to(ROOT_DIR)), block))

    return snippets


YAML_SNIPPETS = collect_all_yaml_snippets()


@pytest.mark.parametrize(["filename", "snippet"], YAML_SNIPPETS)
def test_yaml_snippets_are_valid(filename: str, snippet: str):
    try:
        yaml.safe_load(snippet)
    except yaml.YAMLError as exc:
        pytest.fail(f"Invalid YAML in {filename}:\n{exc}")
