from __future__ import annotations

from pathlib import Path

import pytest

from schemathesis.core.cache import Entry, Kind, Manifest, Request, write
from schemathesis.core.version import SCHEMATHESIS_VERSION


def _seed(directory: Path, entries: list[Entry]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        format_version=1,
        schemathesis_version=SCHEMATHESIS_VERSION,
        schema_location="openapi.yaml",
        base_url="http://example.com",
        created_at="2026-05-05T10:00:00Z",
    )
    write(directory, manifest, entries)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_no_cache_row_when_cache_empty(ctx, cli, snapshot_cli, tmp_path):
    api = ctx.openapi.apps.success()
    assert (
        cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(tmp_path / "cache")}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_cache_row_shows_replayed_count(ctx, cli, snapshot_cli, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    cache_dir = tmp_path / "cache"
    _seed(
        cache_dir,
        [
            Entry(
                id=1,
                kind=Kind.METHOD_NOT_ALLOWED,
                operation="POST /missing",
                request=Request(
                    method="POST",
                    headers={"content-type": "application/json"},
                    body={"name": "x"},
                ),
            )
        ],
    )

    assert (
        cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(cache_dir)}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_cache_row_shows_stale_removed(ctx, cli, snapshot_cli, tmp_path):
    # Operation that does not exist in the success() schema -> dropped at pre-flight.
    api = ctx.openapi.apps.success()
    cache_dir = tmp_path / "cache"
    _seed(
        cache_dir,
        [
            Entry(
                id=1,
                kind=Kind.METHOD_NOT_ALLOWED,
                operation="POST /vanished",
                request=Request(method="POST"),
            )
        ],
    )

    assert (
        cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(cache_dir)}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_cache_row_shows_unavailable_when_corrupt(ctx, cli, snapshot_cli, tmp_path):
    api = ctx.openapi.apps.success()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "manifest.json").write_text("not json", encoding="utf-8")
    (cache_dir / "entries.jsonl").write_text("", encoding="utf-8")

    assert (
        cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(cache_dir)}},
        )
        == snapshot_cli
    )
