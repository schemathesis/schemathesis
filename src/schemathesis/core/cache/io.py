from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from schemathesis.core import storage
from schemathesis.core.cache.models import Entry, Manifest, Request
from schemathesis.core.output.sanitization import is_sensitive_key

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig

MANIFEST_FILENAME = "manifest.json"
ENTRIES_FILENAME = "entries.jsonl"
FEATURE_NAME = "cache"


def effective_directory(cache_directory: Path | None, project_title: str | None) -> Path:
    """Honor user override; otherwise `<root>/<project-slug>/cache/`."""
    if cache_directory is not None:
        return cache_directory
    return storage.project_directory(storage.DEFAULT_ROOT, project_title) / FEATURE_NAME


def load(directory: Path) -> tuple[Manifest, list[Entry]] | None:
    manifest_path = directory / MANIFEST_FILENAME
    entries_path = directory / ENTRIES_FILENAME
    try:
        if not manifest_path.is_file() or not entries_path.is_file():
            return None
        manifest = Manifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        entries = _load_entries(entries_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return manifest, entries


def write(directory: Path, manifest: Manifest, entries: list[Entry]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    storage.atomic_write_text(directory / MANIFEST_FILENAME, json.dumps(manifest.to_dict(), indent=2))
    lines: list[str] = []
    for entry in entries:
        try:
            lines.append(json.dumps(entry.to_dict()))
        except (TypeError, ValueError):
            continue
    storage.atomic_write_text(directory / ENTRIES_FILENAME, "".join(line + "\n" for line in lines))


def sanitize_request(request: Request, config: SanitizationConfig) -> Request:
    keys = config.keys_to_sanitize
    markers = config.sensitive_markers
    return Request(
        method=request.method,
        path_parameters=request.path_parameters,
        query={k: v for k, v in request.query.items() if not is_sensitive_key(k, keys, markers)},
        headers={k: v for k, v in request.headers.items() if not is_sensitive_key(k, keys, markers)},
        cookies={k: v for k, v in request.cookies.items() if not is_sensitive_key(k, keys, markers)},
        body=_sanitize_body(request.body, keys, markers),
    )


def _sanitize_body(body: Any, keys: tuple[str, ...], markers: tuple[str, ...]) -> Any:
    if isinstance(body, dict):
        return {k: _sanitize_body(v, keys, markers) for k, v in body.items() if not is_sensitive_key(k, keys, markers)}
    if isinstance(body, list):
        return [_sanitize_body(item, keys, markers) for item in body]
    return body


def _load_entries(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        try:
            entries.append(Entry.from_dict(data))
        except ValueError:
            # Forward-compat: an older reader meets a newer kind. Skip the entry rather than
            # rejecting the whole file. JSON decode errors propagate up to `load`.
            continue
    return entries
