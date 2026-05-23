from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import hypothesis
import tracecov
from hypothesis import HealthCheck, Phase, Verbosity
from hypothesis.errors import Unsatisfiable

import schemathesis
from schemathesis.config import SanitizationConfig
from schemathesis.config._generation import GenerationConfig
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema import is_valid
from schemathesis.core.result import Ok
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.builder import generate_example_cases
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases
from schemathesis.specs.openapi.schemas import HTTP_METHODS
from schemathesis.transport.prepare import normalize_base_url, prepare_request
from schemathesis.transport.requests import REQUESTS_TRANSPORT

DEFAULT_FUZZING_MAX_EXAMPLES = 100

# Linux exposes resident-set size via /proc/self/statm (field 2, in pages). Other platforms
# return None and audit_schema skips RSS tracking — the dimension is purely informational.
_STATM_AVAILABLE = sys.platform == "linux" and os.path.exists("/proc/self/statm")
_PAGESIZE = os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096


def _rss_bytes() -> int | None:
    if not _STATM_AVAILABLE:
        return None
    with open("/proc/self/statm") as fd:
        return int(fd.readline().split()[1]) * _PAGESIZE


_NO_SANITIZATION = SanitizationConfig(enabled=False)


# Media types stripped from the audit's CoverageMap so they don't appear as gaps. Other
# unsupported types stay in the spec and surface via `unknown_unsupported_media_types`,
# so missing-serializer candidates remain visible.
_KNOWN_UNSUPPORTED_MEDIA_TYPES: dict[str, str] = {
    "application/x-msgpack": "binary msgpack",
    "application/x-json-smile": "binary Smile encoding",
    "application/vnd.kubernetes.protobuf": "Kubernetes protobuf binary",
    "application/pdf": "opaque PDF binary",
    "application/zip": "opaque ZIP archive",
    "application/x-tar": "opaque TAR archive",
    "application/dicom": "DICOM medical image",
    "application/ndjson": "newline-delimited JSON streaming",
}

_KNOWN_UNSUPPORTED_PREFIXES: tuple[str, ...] = (
    "image/",
    "audio/",
    "video/",
    "message/",
)


class PhaseName(str, Enum):
    FUZZING = "fuzzing"
    COVERAGE = "coverage"
    EXAMPLES = "examples"


@dataclass(slots=True)
class AuditError:
    # Where in the audit the failure happened: `load_failed`, `operation_build_failed`,
    # `stats_failed`, `html_report_failed`, `worker_crashed`, or `METHOD /path` for a
    # per-operation generation failure.
    stage: str
    # Exception class name; None when the failure isn't an exception (e.g. a crashed worker
    # whose cause we couldn't capture).
    exception: str | None
    message: str
    # Operation coordinates when known; used by the audit to attribute gaps to the failing
    # operation so a schema isn't reported as incomplete just because one $ref is broken.
    path: str | None = None
    method: str | None = None


def error_from_exc(stage: str, exc: BaseException, *, path: str | None = None, method: str | None = None) -> AuditError:
    if path is None:
        path = getattr(exc, "path", None)
    if method is None:
        method = getattr(exc, "method", None)
    return AuditError(stage=stage, exception=exc.__class__.__name__, message=str(exc), path=path, method=method)


@dataclass(slots=True)
class SchemaResult:
    api: str
    corpus: str
    phase: str
    operations: int = 0
    cases_generated: int = 0
    duration_seconds: float = 0.0
    errors: list[AuditError] = field(default_factory=list)
    # `(METHOD path, mode)` pairs the generator could not satisfy — distinguishes a real coverage
    # gap from a schema with nothing to exercise on that mode.
    unsatisfiable: list[tuple[str, str]] = field(default_factory=list)
    statistic: dict[str, Any] | None = None
    gaps: list[dict[str, Any]] = field(default_factory=list)
    uncovered_keywords: list[dict[str, Any]] = field(default_factory=list)
    # Spec-declared media types with no transport serializer and not on the explicit denylist.
    unknown_unsupported_media_types: list[str] = field(default_factory=list)
    # Spec-declared inline `example` values that fail their own sibling schema. Schemathesis
    # silently skips these, so leaving them in the `examples.total` denominator inflates gaps.
    examples_invalid: int = 0
    # Uncovered-keyword entries dropped because they belong to operations the loader couldn't
    # build (e.g. unresolvable $ref). Lets the live view treat error-only schemas as complete.
    excluded_by_errors: int = 0
    # Per-operation RSS delta in bytes (Linux only). `None` means the sampler was unavailable —
    # an empty list means we sampled but no Ok operation was driven. Values can be negative.
    rss_jumps: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class AuditOutcome:
    result: SchemaResult
    coverage_map: tracecov.CoverageMap | None


def _is_known_unsupported(media_type: str) -> bool:
    if media_type in _KNOWN_UNSUPPORTED_MEDIA_TYPES:
        return True
    return any(media_type.startswith(prefix) for prefix in _KNOWN_UNSUPPORTED_PREFIXES)


def _is_serialisable_media_type(media_type: str) -> bool:
    try:
        return REQUESTS_TRANSPORT.get_first_matching_media_type(media_type) is not None
    except MalformedMediaType:
        # Malformed entries get dropped per-case by the runtime filter; leave the audit alone.
        return True


def _strip_known_unsupported_media_types(raw_schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return `(filtered_schema, unknown_unsupported_media_types)`."""
    filtered = deepclone(raw_schema)
    unknown: set[str] = set()

    def _filter_content(content: dict[str, Any]) -> None:
        for media_type in list(content):
            if _is_known_unsupported(media_type):
                del content[media_type]
            elif not _is_serialisable_media_type(media_type):
                unknown.add(media_type)

    def _filter_consumes(consumes: list[Any]) -> list[Any]:
        kept: list[Any] = []
        for media_type in consumes:
            if isinstance(media_type, str) and _is_known_unsupported(media_type):
                continue
            if isinstance(media_type, str) and not _is_serialisable_media_type(media_type):
                unknown.add(media_type)
            kept.append(media_type)
        return kept

    paths = filtered.get("paths")
    if isinstance(paths, dict):
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for method, operation in list(path_item.items()):
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                body = operation.get("requestBody")
                if isinstance(body, dict) and isinstance(body.get("content"), dict):
                    _filter_content(body["content"])
                    if not body["content"]:
                        operation.pop("requestBody", None)
                consumes = operation.get("consumes")
                if isinstance(consumes, list):
                    operation["consumes"] = _filter_consumes(consumes)
    global_consumes = filtered.get("consumes")
    if isinstance(global_consumes, list):
        filtered["consumes"] = _filter_consumes(global_consumes)

    return filtered, sorted(unknown)


def _is_response_gap(gap: dict[str, Any]) -> bool:
    return (gap.get("kind") or "").startswith("response_")


def _strip_invalid_examples(node: Any) -> int:
    """Drop inline `example`/`default` values that fail their sibling schema, return the count.

    Schemathesis silently skips these during generation; tracecov otherwise renders them as
    red cells in the per-API HTML. Stripping mutates `node` in place so both generation and
    coverage tracking see the same view.
    """
    if isinstance(node, dict):
        invalid = 0
        sibling_schema = {k: v for k, v in node.items() if k not in ("example", "examples", "default")}
        for keyword in ("example", "default"):
            if keyword in node and not is_valid(node[keyword], sibling_schema):
                del node[keyword]
                invalid += 1
        for key, value in node.items():
            # Don't descend into example/default payloads themselves (data, not schema).
            if key in ("example", "examples", "default"):
                continue
            invalid += _strip_invalid_examples(value)
        return invalid
    if isinstance(node, list):
        return sum(_strip_invalid_examples(item) for item in node)
    return 0


def _is_response_keyword(entry: dict[str, Any]) -> bool:
    return "/responses/" in (entry.get("schema_path") or "")


def _operation_pointer_prefix(method: str, path: str) -> str:
    """JSON-pointer prefix that all schema paths under (METHOD, path) start with."""
    encoded = path.replace("~", "~0").replace("/", "~1")
    return f"/paths/{encoded}/{method.lower()}/"


def _errored_operation_keys(errors: list[AuditError]) -> set[tuple[str, str]]:
    """(method_lower, path) pairs for every error whose origin operation is known."""
    return {(error.method.lower(), error.path) for error in errors if error.path and error.method}


def _entry_under_errored_op(entry: dict[str, Any], keys: set[tuple[str, str]], prefixes: list[str]) -> bool:
    """Match either by (method, path) on direct gap fields, or by schema_path prefix."""
    method = entry.get("method")
    path = entry.get("path")
    if isinstance(method, str) and isinstance(path, str) and (method.lower(), path) in keys:
        return True
    schema_path = entry.get("schema_path") or ""
    return any(schema_path.startswith(prefix) for prefix in prefixes)


def _attach_query(url: str, params: Any) -> str:
    if not params:
        return url
    if isinstance(params, str):
        query_str = params
    elif isinstance(params, Mapping):
        query_str = urlencode(list(params.items()), doseq=True)
    else:
        query_str = urlencode(params, doseq=True)
    if not query_str:
        return url
    scheme, netloc, path, existing, fragment = urlsplit(url)
    merged = f"{existing}&{query_str}" if existing else query_str
    return urlunsplit((scheme, netloc, path, merged, fragment))


def _audit_body(kwargs: dict[str, Any]) -> tuple[bytes | None, str | None]:
    """Encode the body for an audit interaction. Returns (body_bytes, fallback_reason)."""
    if "files" in kwargs:
        return None, "multipart files require requests' encoder"
    if "json" in kwargs:
        try:
            return json.dumps(kwargs["json"], allow_nan=False).encode("utf-8"), None
        except (TypeError, ValueError) as exc:
            return None, f"json encoding failed: {exc}"
    data = kwargs.get("data")
    if data is None:
        return None, None
    if isinstance(data, bytes):
        return data, None
    if isinstance(data, str):
        return data.encode("utf-8"), None
    if isinstance(data, Mapping):
        try:
            return urlencode(list(data.items()), doseq=True).encode("utf-8"), None
        except (TypeError, ValueError) as exc:
            return None, f"form encoding failed: {exc}"
    return None, f"unsupported data type {type(data).__name__}"


def _case_to_interaction(case: Case) -> tracecov.HttpInteraction:
    # Skip the modification-detection hash on `case.meta` access: the audit never mutates
    # cases or revalidates them, so the cost (canonical-JSON encoding bodies the size of an
    # Azure ApplicationGateway) is pure waste.
    object.__setattr__(case, "_freeze_metadata", True)
    base_url = normalize_base_url(case.operation.base_url)
    kwargs = REQUESTS_TRANSPORT.serialize_case(case, base_url=base_url, headers=None)
    body, fallback = _audit_body(kwargs)
    if fallback is not None:
        # Multipart/files and exotic encodings fall back to the requests machinery so the wire
        # bytes still match what a real call would send.
        prepared = prepare_request(case, headers=None, config=_NO_SANITIZATION)
        body = prepared.body.encode("utf-8") if isinstance(prepared.body, str) else prepared.body
        if body is None and prepared.headers.get("Content-Length") == "0":
            body = b""
        return tracecov.HttpInteraction(
            request=tracecov.HttpRequest(
                method=case.method, url=prepared.url, body=body, headers=dict(prepared.headers)
            ),
            response=None,
            timestamp=time.time(),
        )
    url = _attach_query(kwargs["url"], kwargs.get("params"))
    headers = dict(kwargs["headers"])
    if body is not None:
        headers.setdefault("Content-Length", str(len(body)))
    elif kwargs.get("data") == {} or kwargs.get("data") == "":
        body = b""
        headers.setdefault("Content-Length", "0")
    return tracecov.HttpInteraction(
        request=tracecov.HttpRequest(method=case.method, url=url, body=body, headers=headers),
        response=None,
        timestamp=time.time(),
    )


def _coverage_cases(
    operation: APIOperation,
    generation_config: GenerationConfig,
    generation_modes: list[GenerationMode],
) -> Generator[Case]:
    transport = operation.schema.transport
    for case in iter_coverage_cases(
        operation=operation,
        generation_modes=generation_modes,
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=generation_config,
        unexpected_methods_seen=None,
    ):
        # Mirror the runner: drop cases for media types with no registered serializer
        # so a single unsupported alternative (e.g. `application/x-msgpack`) doesn't
        # crash the operation and forfeit coverage of its serializable siblings.
        if case.media_type and transport.get_first_matching_media_type(case.media_type) is None:
            continue
        yield case


def _example_cases(operation: APIOperation) -> Generator[Case]:
    def _noop(case: Case) -> None: ...

    yield from generate_example_cases(test=_noop, operation=operation, fill_missing=False)


def _draw(strategy: Any, fuzz_settings: hypothesis.settings) -> list[Case]:
    cases: list[Case] = []

    @hypothesis.given(strategy)  # type: ignore[untyped-decorator]
    @fuzz_settings  # type: ignore[untyped-decorator]
    def collect(case: Case) -> None:
        cases.append(case)

    collect._hypothesis_internal_database_key = b""
    collect()
    return cases


def _fuzzing_cases(
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    max_examples: int,
    unsatisfiable: list[GenerationMode],
) -> Generator[Case]:
    fuzz_settings = hypothesis.settings(
        database=None,
        max_examples=max_examples,
        deadline=None,
        verbosity=Verbosity.quiet,
        phases=(Phase.generate,),
        suppress_health_check=list(HealthCheck),
    )
    for mode in generation_modes:
        try:
            yield from _draw(operation.as_strategy(generation_mode=mode), fuzz_settings)
        except Unsatisfiable:
            unsatisfiable.append(mode)


def _cases_for_phase(
    phase: PhaseName,
    operation: APIOperation,
    generation_config: GenerationConfig,
    generation_modes: list[GenerationMode],
    max_examples: int,
    unsatisfiable: list[GenerationMode],
) -> Generator[Case]:
    match phase:
        case PhaseName.COVERAGE:
            yield from _coverage_cases(operation, generation_config, generation_modes)
        case PhaseName.EXAMPLES:
            yield from _example_cases(operation)
        case PhaseName.FUZZING:
            yield from _fuzzing_cases(operation, generation_modes, max_examples, unsatisfiable)


def audit_schema(
    raw_schema: dict[str, Any],
    *,
    api: str,
    corpus: str,
    phase: PhaseName,
    generation_modes: list[GenerationMode] | None = None,
    fuzzing_max_examples: int = DEFAULT_FUZZING_MAX_EXAMPLES,
) -> AuditOutcome:
    if generation_modes is None:
        generation_modes = list(GenerationMode)
    result = SchemaResult(api=api, corpus=corpus, phase=phase.value)
    started = time.monotonic()
    try:
        filtered_schema, unknown_unsupported = _strip_known_unsupported_media_types(raw_schema)
        result.unknown_unsupported_media_types = unknown_unsupported
        result.examples_invalid = _strip_invalid_examples(filtered_schema)
        schema = schemathesis.openapi.from_dict(filtered_schema)
        coverage_map = tracecov.CoverageMap.from_dict(filtered_schema)
    except Exception as exc:
        result.errors.append(error_from_exc("load_failed", exc))
        result.duration_seconds = time.monotonic() - started
        return AuditOutcome(result=result, coverage_map=None)

    generation_config = GenerationConfig()
    rss_jumps: list[dict[str, Any]] | None = [] if _STATM_AVAILABLE else None
    for operation_result in schema.get_all_operations():
        if not isinstance(operation_result, Ok):
            err = operation_result.err()
            result.errors.append(error_from_exc("operation_build_failed", err))
            continue
        operation = operation_result.ok()
        result.operations += 1
        unsatisfiable: list[GenerationMode] = []
        op_method = operation.method.upper()
        op_path = operation.full_path
        rss_before = _rss_bytes()
        try:
            for case in _cases_for_phase(
                phase, operation, generation_config, generation_modes, fuzzing_max_examples, unsatisfiable
            ):
                interaction = _case_to_interaction(case)
                coverage_map.record_schemathesis_interactions(case.method, op_path, [interaction])
                result.cases_generated += 1
        except Exception as exc:
            result.errors.append(error_from_exc(f"{op_method} {op_path}", exc, path=op_path, method=op_method))
        for mode in unsatisfiable:
            result.unsatisfiable.append((f"{op_method} {op_path}", mode.value))
        if rss_jumps is not None and rss_before is not None:
            rss_after = _rss_bytes()
            if rss_after is not None:
                rss_jumps.append({"method": op_method, "path": op_path, "delta_bytes": rss_after - rss_before})

    result.rss_jumps = rss_jumps

    try:
        statistic = coverage_map.statistic()
        # The audit ignores response coverage — no real responses are ever observed.
        statistic.pop("responses", None)
        result.statistic = statistic
        result.gaps = [gap for gap in coverage_map.coverage_gaps() if not _is_response_gap(gap)]
        result.uncovered_keywords = [
            entry for entry in coverage_map.uncovered_keywords() if not _is_response_keyword(entry)
        ]
    except Exception as exc:
        result.errors.append(error_from_exc("stats_failed", exc))

    keys = _errored_operation_keys(result.errors)
    if keys:
        prefixes = [_operation_pointer_prefix(method, path) for method, path in keys]
        kept_keywords = [
            entry for entry in result.uncovered_keywords if not _entry_under_errored_op(entry, keys, prefixes)
        ]
        kept_gaps = [gap for gap in result.gaps if not _entry_under_errored_op(gap, keys, prefixes)]
        result.excluded_by_errors = (
            len(result.uncovered_keywords) - len(kept_keywords) + len(result.gaps) - len(kept_gaps)
        )
        result.uncovered_keywords = kept_keywords
        result.gaps = kept_gaps

    result.duration_seconds = time.monotonic() - started
    return AuditOutcome(result=result, coverage_map=coverage_map)
