from __future__ import annotations

import json
from pathlib import Path

import pytest

from schemathesis.config import SanitizationConfig
from schemathesis.core import storage
from schemathesis.core.cache import (
    ENTRIES_FILENAME,
    FORMAT_VERSION,
    MANIFEST_FILENAME,
    Entry,
    Kind,
    Manifest,
    Request,
    effective_directory,
    load,
    sanitize_request,
    write,
)
from schemathesis.core.version import SCHEMATHESIS_VERSION


def _manifest(**overrides) -> Manifest:
    base = {
        "format_version": FORMAT_VERSION,
        "schemathesis_version": SCHEMATHESIS_VERSION,
        "schema_location": "openapi.yaml",
        "base_url": "http://127.0.0.1:8080",
        "created_at": "2026-05-05T10:00:00Z",
    }
    base.update(overrides)
    return Manifest(**base)


def _entry(**overrides) -> Entry:
    base = {
        "id": 1,
        "kind": Kind.ERROR_FEEDBACK,
        "operation": "POST /users",
        "request": Request(
            method="POST",
            headers={"content-type": "application/json"},
            body={"email": "not-email"},
        ),
    }
    base.update(overrides)
    return Entry(**base)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("API v1", "api-v1"),
        ("Pet Store", "pet-store"),
        ("Already-Slugged", "already-slugged"),
        ("  whitespace  ", "whitespace"),
        ("___", "default"),
    ],
)
def test_slug(name, expected):
    assert storage.slug(name) == expected


@pytest.mark.parametrize(
    ("override", "title", "expected_segments"),
    [
        (None, None, ("default", "cache")),
        (None, "API v1", ("api-v1", "cache")),
        (Path("/custom"), "ignored", None),
    ],
    ids=["default-project", "named-project", "override-wins"],
)
def test_effective_directory(override, title, expected_segments):
    if expected_segments is None:
        assert effective_directory(override, title) == override
    else:
        assert effective_directory(override, title) == storage.DEFAULT_ROOT.joinpath(*expected_segments)


def test_roundtrip(tmp_path):
    manifest = _manifest()
    entries = [_entry(), _entry(id=2, kind=Kind.METHOD_NOT_ALLOWED, operation="DELETE /users")]
    write(tmp_path, manifest, entries)
    assert load(tmp_path) == (manifest, entries)


def _setup_missing_files(tmp_path):
    pass


def _setup_corrupt_manifest(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("not json")
    (tmp_path / ENTRIES_FILENAME).write_text("")


def _setup_corrupt_entries(tmp_path):
    write(tmp_path, _manifest(), [_entry()])
    (tmp_path / ENTRIES_FILENAME).write_text("garbage\n")


def _setup_unknown_format_version(tmp_path):
    write(tmp_path, _manifest(format_version=999), [])


@pytest.mark.parametrize(
    "setup",
    [_setup_missing_files, _setup_corrupt_manifest, _setup_corrupt_entries, _setup_unknown_format_version],
    ids=["missing-files", "corrupt-manifest", "corrupt-entries", "unknown-format-version"],
)
def test_load_returns_none(tmp_path, setup):
    setup(tmp_path)
    assert load(tmp_path) is None


def test_load_skips_entries_with_unknown_kind(tmp_path):
    write(tmp_path, _manifest(), [_entry(id=1), _entry(id=2)])
    raw = (tmp_path / ENTRIES_FILENAME).read_text().splitlines()
    rewritten = raw[0:1] + [json.dumps({**json.loads(raw[1]), "kind": "future_kind"})]
    (tmp_path / ENTRIES_FILENAME).write_text("\n".join(rewritten) + "\n")
    result = load(tmp_path)
    assert result is not None
    _, entries = result
    assert [entry.id for entry in entries] == [1]


@pytest.mark.parametrize(
    ("request_in", "expected"),
    [
        (
            Request(
                method="POST",
                headers={"content-type": "application/json", "Authorization": "Bearer xyz"},
                body={"a": 1},
            ),
            Request(
                method="POST",
                headers={"content-type": "application/json"},
                body={"a": 1},
            ),
        ),
        (
            Request(
                method="POST",
                headers={"content-type": "application/json"},
                body={"username": "alice", "password": "hunter2", "csrf_token": "xyz"},
            ),
            Request(
                method="POST",
                headers={"content-type": "application/json"},
                body={"username": "alice"},
            ),
        ),
        (
            Request(
                method="GET",
                headers={"content-type": "application/json", "Cookie": "session=xyz"},
                body={"a": 1},
            ),
            Request(
                method="GET",
                headers={"content-type": "application/json"},
                body={"a": 1},
            ),
        ),
        (
            Request(method="GET", query={"api_key": "secret", "limit": "10"}),
            Request(method="GET", query={"limit": "10"}),
        ),
        (
            Request(method="GET", cookies={"session": "abc", "track": "xyz"}, body={"a": 1}),
            Request(method="GET", cookies={"track": "xyz"}, body={"a": 1}),
        ),
        (
            Request(method="GET", path_parameters={"id": "42"}, headers={"authorization": "Bearer xyz"}),
            Request(method="GET", path_parameters={"id": "42"}),
        ),
        (
            Request(method="POST", headers={"content-type": "application/json"}, body={"a": 1}),
            Request(method="POST", headers={"content-type": "application/json"}, body={"a": 1}),
        ),
    ],
    ids=[
        "drops-auth-header-keeps-content-type",
        "drops-body-password-and-csrf",
        "drops-cookie-header",
        "drops-sensitive-query-param",
        "drops-sensitive-cookie",
        "drops-auth-header-keeps-path-params",
        "no-op-on-clean-request",
    ],
)
def test_sanitize_request(request_in, expected):
    assert sanitize_request(request_in, SanitizationConfig()) == expected
