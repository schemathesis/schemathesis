import re
from pathlib import Path

import pytest

from schemathesis.config import SchemathesisConfig

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


def extract_examples(path: str) -> list:
    with open(DOCS_DIR / path) as fd:
        markdown = fd.read()
    pattern = re.compile(r"```toml(.*?)```", re.DOTALL)
    return pattern.findall(markdown)


ALL_CONFIGS = extract_examples("reference/configuration.md")
DEFAULT_CONFIG = SchemathesisConfig()


@pytest.mark.parametrize("config", ALL_CONFIGS, ids=[f"example_{i}" for i in range(len(ALL_CONFIGS))])
def test_configs(monkeypatch, config, snapshot_config):
    monkeypatch.setenv("TEST_STRING_1", "foo")
    monkeypatch.setenv("TEST_STRING_2", "bar")
    config = SchemathesisConfig.from_str(config)
    assert config == snapshot_config
