from __future__ import annotations

from schemathesis.core.cache import CacheWriter, Entry, Kind, Manifest, Request, load, write
from schemathesis.core.version import SCHEMATHESIS_VERSION


def test_write_skips_entries_with_non_json_serializable_body(tmp_path):
    manifest = Manifest(
        format_version=1,
        schemathesis_version=SCHEMATHESIS_VERSION,
        schema_location="openapi.yaml",
        base_url="http://example.com",
        created_at="2026-05-05T10:00:00Z",
    )
    good = Entry(id=1, kind=Kind.ERROR_FEEDBACK, operation="POST /a", request=Request(method="POST", body={"x": 1}))
    bad = Entry(id=2, kind=Kind.ERROR_FEEDBACK, operation="POST /b", request=Request(method="POST", body=b"\x00\x01"))

    write(tmp_path, manifest, [good, bad])

    loaded = load(tmp_path)
    assert loaded is not None
    _, entries = loaded
    assert [entry.id for entry in entries] == [1]


def test_record_singleton_dedups_concurrent_writes_for_same_operation():
    writer = CacheWriter()
    request = Request(method="GET", headers={"content-type": "application/json"})
    writer.record(Kind.AUTH_REQUIRED, "GET /protected", request)
    writer.record(Kind.AUTH_REQUIRED, "GET /protected", request)
    pending = writer.drain()
    assert len(pending) == 1
    assert pending[0].kind is Kind.AUTH_REQUIRED
    assert pending[0].operation == "GET /protected"
