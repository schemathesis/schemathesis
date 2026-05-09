from __future__ import annotations

import time
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import hypothesis
import tracecov
from hypothesis import HealthCheck, Phase, Verbosity
from hypothesis.errors import Unsatisfiable

import schemathesis
from schemathesis.config import SanitizationConfig
from schemathesis.config._generation import GenerationConfig
from schemathesis.core.result import Ok
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.builder import generate_example_cases
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases
from schemathesis.transport.prepare import prepare_request

DEFAULT_FUZZING_MAX_EXAMPLES = 100

_NO_SANITIZATION = SanitizationConfig(enabled=False)


class PhaseName(str, Enum):
    FUZZING = "fuzzing"
    COVERAGE = "coverage"
    EXAMPLES = "examples"


@dataclass(slots=True)
class SchemaResult:
    api: str
    corpus: str
    phase: str
    operations: int = 0
    cases_generated: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    # `(METHOD path, mode)` pairs the generator could not satisfy — distinguishes a real coverage
    # gap from a schema with nothing to exercise on that mode.
    unsatisfiable: list[tuple[str, str]] = field(default_factory=list)
    statistic: dict[str, Any] | None = None
    gaps: list[dict[str, Any]] = field(default_factory=list)
    uncovered_keywords: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AuditOutcome:
    result: SchemaResult
    coverage_map: tracecov.CoverageMap | None


def _is_response_gap(gap: dict[str, Any]) -> bool:
    return (gap.get("kind") or "").startswith("response_")


def _is_response_keyword(entry: dict[str, Any]) -> bool:
    return "/responses/" in (entry.get("schema_path") or "")


def _case_to_interaction(case: Case) -> tracecov.HttpInteraction:
    prepared = prepare_request(case, headers=None, config=_NO_SANITIZATION)
    body = prepared.body.encode("utf-8") if isinstance(prepared.body, str) else prepared.body
    # `requests` collapses an empty form body to None even though the wire still carries
    # zero bytes — pass b"" so the recorder parses it as `{}` and `/required` flips invalid.
    if body is None and prepared.headers.get("Content-Length") == "0":
        body = b""
    return tracecov.HttpInteraction(
        request=tracecov.HttpRequest(
            method=case.method,
            url=prepared.url,
            body=body,
            headers=dict(prepared.headers),
        ),
        response=None,
        timestamp=time.time(),
    )


def _coverage_cases(
    operation: APIOperation,
    generation_config: GenerationConfig,
    generation_modes: list[GenerationMode],
) -> Generator[Case]:
    yield from iter_coverage_cases(
        operation=operation,
        generation_modes=generation_modes,
        generate_duplicate_query_parameters=False,
        unexpected_methods=set(),
        generation_config=generation_config,
        unexpected_methods_seen=None,
    )


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
        schema = schemathesis.openapi.from_dict(raw_schema)
        coverage_map = tracecov.CoverageMap.from_dict(raw_schema)
    except Exception as exc:
        result.errors.append(f"load_failed: {exc.__class__.__name__}: {exc}")
        result.duration_seconds = time.monotonic() - started
        return AuditOutcome(result=result, coverage_map=None)

    generation_config = GenerationConfig()
    for operation_result in schema.get_all_operations():
        if not isinstance(operation_result, Ok):
            continue
        operation = operation_result.ok()
        result.operations += 1
        unsatisfiable: list[GenerationMode] = []
        try:
            for case in _cases_for_phase(
                phase, operation, generation_config, generation_modes, fuzzing_max_examples, unsatisfiable
            ):
                interaction = _case_to_interaction(case)
                coverage_map.record_schemathesis_interactions(case.method, operation.full_path, [interaction])
                result.cases_generated += 1
        except Exception as exc:
            result.errors.append(f"{operation.method} {operation.full_path}: {exc.__class__.__name__}")
        for mode in unsatisfiable:
            result.unsatisfiable.append((f"{operation.method.upper()} {operation.full_path}", mode.value))

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
        result.errors.append(f"stats_failed: {exc.__class__.__name__}: {exc}")

    result.duration_seconds = time.monotonic() - started
    return AuditOutcome(result=result, coverage_map=coverage_map)
