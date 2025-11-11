import re
from pathlib import Path

import pytest
import yaml

import schemathesis.config._projects
from schemathesis.checks import CHECKS
from schemathesis.config import SchemathesisConfig
from schemathesis.config._validator import CONFIG_SCHEMA
from schemathesis.core.errors import HookError
from schemathesis.core.transforms import resolve_pointer

ROOT_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = ROOT_DIR / "docs"
README_FILE = ROOT_DIR / "README.md"


def read_doc(path: str) -> str:
    with open(DOCS_DIR / path, encoding="utf-8") as fd:
        return fd.read()


def extract_examples(path: str, format: str = "toml") -> list:
    markdown = read_doc(path)
    pattern = re.compile(rf"```{format}(.*?)```", re.DOTALL)
    return pattern.findall(markdown)


ALL_CONFIGS = (
    extract_examples("reference/configuration.md")
    + extract_examples("reference/warnings.md")
    + extract_examples("configuration.md")
)
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
    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin-key")
    monkeypatch.setenv("USERNAME", "user-1")
    monkeypatch.setenv("PASSWORD", "secret-password")
    monkeypatch.setenv("CLIENT_ID", "admin")
    monkeypatch.setenv("CLIENT_SECRET", "secret!")
    monkeypatch.setenv("TOKEN", "secret-token!")
    monkeypatch.setenv("SESSION_ID", "secret-session-id!")
    monkeypatch.setenv("USER_ID", "42")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(schemathesis.config._projects, "get_workers_count", lambda: 4)
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


def _resolve(current):
    if "$ref" in current:
        return resolve_pointer(CONFIG_SCHEMA, current["$ref"][1:])
    return current


def _search_rest(current, variant, title, segment):
    # Check if current uses additionalProperties (e.g., auth.openapi.<scheme>)
    if "additionalProperties" in current and "properties" not in current:
        data = _resolve(current["additionalProperties"])
    else:
        data = _resolve(current["properties"][variant])
    rest = title.split(segment, 1)[1].strip(".")
    if rest:
        for rest_segment in rest.split("."):
            if "properties" not in data or rest_segment not in data["properties"]:
                # Field doesn't exist in this variant (e.g., operation-ordering not in StatefulPhaseConfig)
                # This is expected for phase-specific fields
                return
            data = data["properties"][rest_segment]


def test_titles_are_valid():
    markdown = read_doc("reference/configuration.md")
    for title in re.findall(r"^####\s+`?([^`\n]+)`?", markdown, re.MULTILINE):
        current = CONFIG_SCHEMA
        for segment in title.split("."):
            current = _resolve(current)
            if segment.startswith("<"):
                # Like `<format>`
                name = segment.strip("<>")
                if name == "format":
                    variants = ["junit", "har", "vcr"]
                elif name == "phase":
                    variants = ["examples", "fuzzing", "coverage", "stateful"]
                elif name == "check":
                    if "expected-statuses" in title:
                        variants = [
                            name
                            for name in CHECKS.get_all_names()
                            if current["properties"][name]["$ref"] == "#/$defs/CheckConfig"
                        ]
                    else:
                        variants = CHECKS.get_all_names()
                elif name == "scheme":
                    # For auth.openapi.<scheme> - use a sample scheme name
                    variants = ["ApiKeyAuth"]
                else:
                    raise ValueError(f"Unknown segment: {name}")

                for variant in variants:
                    _search_rest(current, variant, title, segment)
                break
            else:
                current = current["properties"][segment]
