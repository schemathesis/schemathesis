from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any, cast

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core import NotSet
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CoveragePhaseData
from schemathesis.transport.serialization import Binary
from tools.coverage.caches import clear_internal_caches

# (operation, scenario, mode, parameter_location, parameter, location, digest); absent fields are "".
Row = tuple[str, str, str, str, str, str, str]


@dataclass(slots=True)
class SchemaFingerprint:
    rows: list[Row]
    errors: list[str]


@dataclass(slots=True)
class RowDiff:
    removed: list[Row]
    added: list[Row]

    def __bool__(self) -> bool:
        return bool(self.removed or self.added)


def _normalize(value: Any) -> Any:
    if isinstance(value, Binary):
        return {"__binary__": value.data.hex()}
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, NotSet):
        return {"__notset__": True}
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _digest(case: Case) -> str:
    request = {
        "method": case.method,
        "path": case.path,
        "path_parameters": case.path_parameters,
        "query": case.query,
        "headers": dict(case.headers),
        "cookies": case.cookies,
        "media_type": case.media_type,
        "body": case.body,
    }
    encoded = json.dumps(_normalize(request), sort_keys=True, separators=(",", ":"), default=repr)
    return blake2b(encoded.encode(), digest_size=8).hexdigest()


def fingerprint_case(case: Case) -> Row:
    meta = case.meta
    assert meta is not None and isinstance(meta.phase.data, CoveragePhaseData)
    data = meta.phase.data
    return (
        f"{case.method} {case.path}",
        data.scenario.value,
        meta.generation.mode.value,
        data.parameter_location.value if data.parameter_location is not None else "",
        data.parameter or "",
        data.location or "",
        _digest(case),
    )


def fingerprint_schema(
    raw_schema: dict[str, Any], *, generation_modes: list[GenerationMode] | None = None
) -> SchemaFingerprint:
    # Generation caches are process-global and seeded draws are reused across schemas; start clean so the
    # fingerprint depends on this schema alone, not on what was processed before it.
    clear_internal_caches()
    modes = list(GenerationMode) if generation_modes is None else generation_modes
    rows: list[Row] = []
    errors: list[str] = []
    try:
        schema = schemathesis.openapi.from_dict(raw_schema)
    except Exception as exc:
        return SchemaFingerprint(rows=[], errors=[f"load_failed: {type(exc).__name__}: {exc}"])
    transport = schema.transport
    config = GenerationConfig()
    for result in schema.get_all_operations():
        if not isinstance(result, Ok):
            errors.append(f"operation_build_failed: {result.err()}")
            continue
        operation = result.ok()
        try:
            for case in schema.iter_coverage_cases(operation, generation_modes=modes, generation_config=config):
                # Mirror the runner: cases for media types with no serializer are never sent.
                if case.media_type and transport.get_first_matching_media_type(case.media_type) is None:
                    continue
                rows.append(fingerprint_case(case))
        except Exception as exc:
            errors.append(f"{operation.method.upper()} {operation.full_path}: {type(exc).__name__}: {exc}")
    rows.sort()
    return SchemaFingerprint(rows=rows, errors=errors)


def diff_rows(baseline: Iterable[Iterable[str]], current: Iterable[Iterable[str]]) -> RowDiff:
    before = Counter(cast("Row", tuple(row)) for row in baseline)
    after = Counter(cast("Row", tuple(row)) for row in current)
    return RowDiff(removed=sorted((before - after).elements()), added=sorted((after - before).elements()))
