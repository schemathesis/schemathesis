from __future__ import annotations

import json

import pytest

from tools.corpus.locator import load_schema_dict, parse_corpus_path


def test_parse_corpus_path_splits_corpus_and_member():
    assert parse_corpus_path("corpus://openapi-3.0/foo.json") == ("openapi-3.0", "foo.json")


def test_parse_corpus_path_keeps_nested_member_path_intact():
    assert parse_corpus_path("corpus://openapi-3.0/vendor/api/1.0.json") == (
        "openapi-3.0",
        "vendor/api/1.0.json",
    )


@pytest.mark.parametrize(
    "spec",
    ["", "no-scheme", "corpus://", "corpus://openapi-3.0/", "corpus:///foo.json"],
)
def test_parse_corpus_path_rejects_invalid(spec):
    with pytest.raises(ValueError):
        parse_corpus_path(spec)


def test_load_schema_dict_from_corpus():
    entry = load_schema_dict("corpus://openapi-3.0/1password.com/events/1.2.0.json")
    assert entry.corpus == "openapi-3.0"
    assert entry.name == "1password.com/events/1.2.0.json"
    assert entry.api == "1password.com/events/1.2.0"
    assert entry.schema["openapi"].startswith("3.")


def test_load_schema_dict_from_json_file(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"openapi": "3.0.0", "paths": {"/x": {}}}))

    entry = load_schema_dict(str(path))
    assert entry.corpus == "external"
    assert entry.schema == {"openapi": "3.0.0", "paths": {"/x": {}}}


def test_load_schema_dict_from_yaml_file(tmp_path):
    path = tmp_path / "spec.yaml"
    path.write_text("openapi: 3.0.0\npaths:\n  /x:\n    get: {}\n")

    entry = load_schema_dict(str(path))
    assert entry.schema == {"openapi": "3.0.0", "paths": {"/x": {"get": {}}}}
