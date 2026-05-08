from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from tools.corpus.io import get_schema_version, iter_all_corpus_files, iter_corpus_file


def make_tarball(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_iter_corpus_file_reads_json_members(tmp_path):
    make_tarball(tmp_path / "sample.tar.gz", {"api.json": b'{"openapi":"3.0.0","paths":{}}'})

    assert list(iter_corpus_file("sample", data_dir=tmp_path)) == [("api.json", {"openapi": "3.0.0", "paths": {}})]


def test_iter_all_corpus_files_reads_every_tarball(tmp_path):
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
