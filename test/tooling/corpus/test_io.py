from __future__ import annotations

import pytest

from tools.corpus.io import (
    CorpusEntry,
    get_schema_version,
    iter_all_corpus_files,
    iter_corpus_entries_from_refs,
    iter_corpus_file,
    iter_corpus_streaming,
)


def test_iter_corpus_file_reads_json_members(tmp_path, make_tarball):
    make_tarball(tmp_path / "sample.tar.gz", {"api.json": b'{"openapi":"3.0.0","paths":{}}'})

    assert list(iter_corpus_file("sample", data_dir=tmp_path)) == [("api.json", {"openapi": "3.0.0", "paths": {}})]


def test_iter_all_corpus_files_reads_every_tarball(tmp_path, make_tarball):
    make_tarball(tmp_path / "first.tar.gz", {"a.json": b'{"swagger":"2.0","paths":{}}'})
    make_tarball(tmp_path / "second.tar.gz", {"b.json": b'{"openapi":"3.1.0","paths":{}}'})

    assert list(iter_all_corpus_files(data_dir=tmp_path)) == [
        ("first", "a.json", {"swagger": "2.0", "paths": {}}),
        ("second", "b.json", {"openapi": "3.1.0", "paths": {}}),
    ]


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


def test_iter_corpus_entries_from_refs_reads_requested_members(tmp_path, make_tarball):
    make_tarball(
        tmp_path / "openapi-3.0.tar.gz",
        {
            "alpha.json": b'{"openapi":"3.0.0","paths":{"/a":{}}}',
            "ignored.json": b"not json",
            "beta.json": b'{"openapi":"3.0.0","paths":{"/b":{}}}',
        },
    )

    assert list(iter_corpus_entries_from_refs("openapi-3.0", ("beta.json", "alpha.json"), data_dir=tmp_path)) == [
        CorpusEntry(corpus="openapi-3.0", name="beta.json", schema={"openapi": "3.0.0", "paths": {"/b": {}}}),
        CorpusEntry(corpus="openapi-3.0", name="alpha.json", schema={"openapi": "3.0.0", "paths": {"/a": {}}}),
    ]


def test_corpus_entry_api_strips_json_suffix():
    entry = CorpusEntry(corpus="c", name="foo/bar.json", schema={})
    assert entry.api == "foo/bar"
