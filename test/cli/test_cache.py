from __future__ import annotations

import os
import platform
import socket
import sys
from pathlib import Path

import pytest

from schemathesis.core.cache import Entry, Kind, Manifest, Request, load, write
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
@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="Needs non-root POSIX permissions")
def test_unwritable_cache_directory(ctx, cli, snapshot_cli, tmp_path):
    api = ctx.openapi.apps.success()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_dir.chmod(0o555)
    try:
        result = cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(cache_dir)}},
        )
    finally:
        cache_dir.chmod(0o755)

    assert "CLI Handler Error" not in result.stdout
    assert result == snapshot_cli


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


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.skipif(platform.system() == "Windows", reason="chmod does not make a directory read-only on Windows")
def test_run_succeeds_when_cache_directory_is_read_only(ctx, cli, snapshot_cli, tmp_path):
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
    # Crash records go to a subdirectory; keep it writable so only the cache write fails.
    (cache_dir / "crashes").mkdir()
    cache_dir.chmod(0o555)
    try:
        result = cli.run(
            api.schema_url,
            "--max-examples=1",
            "--phases=fuzzing",
            config={"cache": {"directory": str(cache_dir)}},
        )
    finally:
        cache_dir.chmod(0o755)
    assert result == snapshot_cli
    assert [entry.operation for entry in load(cache_dir)[1]] == ["POST /vanished"]


@pytest.mark.parametrize("kind", [Kind.ERROR_FEEDBACK, Kind.AUTH_REQUIRED])
def test_entries_kept_when_replay_cannot_reach_the_server(ctx, cli, tmp_path, kind):
    api = ctx.openapi.apps.success()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    cache_dir = tmp_path / "cache"
    _seed(cache_dir, [Entry(id=1, kind=kind, operation="GET /api/success", request=Request(method="GET"))])

    cli.run(
        api.schema_url,
        f"--url=http://127.0.0.1:{closed_port}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--request-timeout=1",
        config={"cache": {"directory": str(cache_dir)}},
    )

    _, entries = load(cache_dir)
    assert entries == [
        Entry(id=1, kind=kind, operation="GET /api/success", request=Request(method="GET"), last_replayed_run=1)
    ]
