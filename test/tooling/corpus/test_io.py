from __future__ import annotations

import pytest

from tools.corpus.io import (
    CorpusEntry,
    get_schema_version,
    iter_corpus_streaming,
    load_corpus_entry,
    parse_schemas,
)


@pytest.mark.parametrize(
    ("raw_schema", "expected"),
    [
        ({"openapi": "3.1.1"}, "3.1"),
        ({"swagger": "2.0"}, "2.0"),
    ],
    ids=["openapi", "swagger"],
)
def test_get_schema_version_detects_supported_versions(raw_schema, expected):
    assert get_schema_version(raw_schema) == expected


def test_parse_schemas_skips_schema_without_version(tmp_path):
    valid_schema = {"openapi": "3.0.3", "info": {"title": "valid", "version": "1"}, "paths": {}}
    (tmp_path / "valid-openapi.yaml").write_text("openapi: 3.0.3\ninfo: {title: valid, version: '1'}\npaths: {}\n")
    (tmp_path / "versionless-openapi.yaml").write_text("info: {title: invalid, version: '1'}\npaths: {}\n")

    assert parse_schemas(tmp_path) == {"3.0": {"valid-openapi.yaml": valid_schema}}


def test_parse_schemas_reads_yaml_as_yaml_1_2(tmp_path):
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.3\npaths: {}\nx-values: {flag: on, answer: yes, payload: !!binary YWJj}\non: off\n"
    )

    assert parse_schemas(tmp_path) == {
        "3.0": {
            "openapi.yaml": {
                "openapi": "3.0.3",
                "paths": {},
                "x-values": {"flag": "on", "answer": "yes", "payload": "YWJj"},
                "on": "off",
            }
        }
    }


def test_iter_corpus_streaming_filters_by_name_before_decoding(tmp_path, make_tarball):
    make_tarball(
        tmp_path / "openapi-3.0.tar.gz",
        {
            "alpha.json": b'{"openapi":"3.0.0","paths":{"/a":{}}}',
            "beta.json": b'{"openapi":"3.0.0","paths":{"/b":{}}}',
        },
    )
    assert list(iter_corpus_streaming("openapi-3.0", only="alpha", data_dir=tmp_path)) == [
        CorpusEntry(corpus="openapi-3.0", name="alpha.json", schema={"openapi": "3.0.0", "paths": {"/a": {}}}),
    ]


def test_iter_corpus_streaming_respects_limit(tmp_path, make_tarball):
    make_tarball(
        tmp_path / "openapi-3.0.tar.gz",
        {f"x{i}.json": b'{"openapi":"3.0.0","paths":{}}' for i in range(5)},
    )
    entries = list(iter_corpus_streaming("openapi-3.0", limit=2, data_dir=tmp_path))
    assert len(entries) == 2
    assert [entry.api for entry in entries] == ["x0", "x1"]


def test_load_corpus_entry_separates_data_directories(tmp_path, make_tarball):
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    make_tarball(first_directory / "same.tar.gz", {"entry.json": b'{"marker":"first"}'})
    make_tarball(second_directory / "same.tar.gz", {"entry.json": b'{"marker":"second"}'})

    assert load_corpus_entry("same", "entry.json", data_dir=first_directory).schema == {"marker": "first"}
    assert load_corpus_entry("same", "entry.json", data_dir=second_directory).schema == {"marker": "second"}


def test_corpus_entry_api_strips_json_suffix():
    entry = CorpusEntry(corpus="c", name="foo/bar.json", schema={})
    assert entry.api == "foo/bar"
