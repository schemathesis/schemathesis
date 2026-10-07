"""Web Fuzzing Commons (WFC) Report output."""

from __future__ import annotations

import datetime
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.failures import (
    AcceptedNegativeData,
    MalformedJson,
    ResponseTimeExceeded,
    ServerError,
    format_failures,
)
from schemathesis.core.shell import ShellType, detect_shell
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine.events import EngineFinished, EngineStarted, ScenarioFinished
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
    AuthScenario,
    EnsureResourceAvailability,
    IgnoredAuth,
    JsonSchemaError,
    MalformedMediaType,
    MissingContentType,
    MissingHeaderNotRejected,
    MissingHeaders,
    RejectedPositiveData,
    UndefinedContentType,
    UndefinedStatusCode,
    UnsupportedMethodResponse,
    UseAfterFree,
)

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.core.failures import Failure
    from schemathesis.engine import events
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import Statistic
    from schemathesis.generation.case import Case

SCHEMA_VERSION = "0.8.0"
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
VALIDATION_FAILURES = (
    JsonSchemaError,
    MalformedJson,
    UndefinedStatusCode,
    MissingContentType,
    UndefinedContentType,
    MalformedMediaType,
    MissingHeaders,
)
DESTRUCTIVE_METHODS = frozenset({"DELETE", "PUT", "PATCH"})
# Codes 904-905 are Schemathesis-specific: WFC has no category for these oracles yet.
INVALID_INPUT_ACCEPTED = 904
UNMAPPED_METHOD = 905
FAULT_CATEGORY_IDS = {
    100: "HTTP_STATUS_500",
    110: "HTTP_STATUS_NO_405_IF_NO_ALLOW",
    113: "HTTP_NONWORKING_DELETE",
    121: "HTTP_OTHER_STATUS_5XX",
    122: "HTTP_CREATED_RESOURCE_NOT_FOUND",
    200: "SCHEMA_INVALID_RESPONSE",
    201: "SCHEMA_INVALID_ALLOW",
    204: "SCHEMA_STATUS_HAS_406_IF_ACCEPT",
    205: "SCHEMA_STATUS_NO_501_IF_IMPLEMENTED",
    206: "SCHEMA_VALIDATION_BYPASS",
    207: "SCHEMA_VALID_INPUT_REJECTED",
    308: "SECURITY_ANONYMOUS_MODIFICATIONS",
    310: "SECURITY_HIDDEN_ACCESSIBLE_ENDPOINT",
    311: "SECURITY_DECLARED_AUTH_NOT_ENFORCED",
    312: "SECURITY_CALL_TIMEOUT",
    INVALID_INPUT_ACCEPTED: "SCHEMATHESIS_INVALID_INPUT_ACCEPTED",
    UNMAPPED_METHOD: "SCHEMATHESIS_UNSUPPORTED_METHOD_RESPONSE",
}


def operation_id(method: str, path: str) -> str:
    return f"{method.upper()}:{path}"


def _case_operation_id(case: Case) -> str:
    return operation_id(case.method, case.path)


def fault_category(failure: Failure) -> tuple[int, str] | None:
    """WFC fault code and a description of the failure, or `None` when WFC has nothing for it."""
    description = f"{type(failure).__name__}: {failure.title}"
    if isinstance(failure, ServerError):
        code = {500: 100, 501: 205}.get(failure.status_code, 121)
        return code, f"{description} (status {failure.status_code})"
    if isinstance(failure, VALIDATION_FAILURES):
        return 200, description
    if isinstance(failure, AllowHeaderMismatch):
        return 201, description
    if isinstance(failure, RejectedPositiveData):
        return (204 if failure.status_code == 406 else 207), description
    if isinstance(failure, (AcceptedNegativeData, MissingHeaderNotRejected)):
        return (206 if 200 <= failure.status_code < 300 else INVALID_INPUT_ACCEPTED), description
    if isinstance(failure, UnsupportedMethodResponse):
        if failure.failure_reason == "missing_allow_header":
            return 110, description
        if failure.failure_reason == "wrong_status" and 200 <= failure.status_code < 300:
            return 310, description
        return UNMAPPED_METHOD, description
    if isinstance(failure, UseAfterFree):
        return 113, description
    if isinstance(failure, IgnoredAuth):
        # WFC 308 covers modifications sent without credentials; 311 covers every other unenforced declared auth.
        method = (failure.operation or "").split(" ", 1)[0].upper()
        if method in DESTRUCTIVE_METHODS and failure.scenario is AuthScenario.NO_AUTH:
            return 308, description
        return 311, f"{description} ({failure.scenario.value})"
    if isinstance(failure, EnsureResourceAvailability):
        return 122, description
    if isinstance(failure, ResponseTimeExceeded):
        return 312, description
    return None


def _case_faults(
    case_id: str, endpoint: str, failures: list[Failure]
) -> list[tuple[tuple[int, str], dict[str, object]]]:
    faults: list[tuple[tuple[int, str], dict[str, object]]] = []
    seen: set[tuple[int, str]] = set()
    for failure in failures:
        category = fault_category(failure)
        if category is None:
            continue
        code, description = category
        # WFC identifies a fault by code and context, so the context names the operation.
        context = f"{endpoint} -> {description}"
        if (code, context) in seen:
            continue
        seen.add((code, context))
        faults.append(
            (
                (code, context),
                {
                    "operationId": endpoint,
                    "testCaseId": case_id,
                    "faultCategories": [{"id": FAULT_CATEGORY_IDS[code], "code": code, "context": context}],
                },
            )
        )
    return faults


@dataclass(slots=True)
class ExportedFailures:
    script: str
    test_cases: list[dict[str, object]]
    found_faults: list[dict[str, object]]
    distinct_faults: int
    output_calls: int


def export_failures(
    statistic: Statistic, endpoints: dict[str, tuple[str, str]], config: OutputConfig, file_name: str
) -> ExportedFailures:
    """The failures the CLI reports: a shell script reproducing them, their WFC test cases and found faults."""
    lines = [f"#!/usr/bin/env {'fish' if detect_shell() is ShellType.FISH else 'bash'}"]
    test_cases: list[dict[str, object]] = []
    found_faults: list[dict[str, object]] = []
    distinct_faults: set[tuple[int, str]] = set()
    output_calls = 0
    for label in sorted(statistic.failures):
        for index, group in enumerate(statistic.failures[label].values(), 1):
            if group.case_id is None or group.code_sample is None:
                continue
            endpoint, sent_as = endpoints[group.case_id]
            faults = _case_faults(group.case_id, endpoint, group.failures)
            if not faults:
                continue
            found_faults.extend(fault for _, fault in faults)
            distinct_faults.update(key for key, _ in faults)
            explanation = format_failures(
                case_id=f"{index}. Test Case ID: {group.case_id}",
                response=group.response,
                failures=group.failures,
                curl=None,
                auth_identity=group.auth_identity,
                config=config,
            )
            lines.append("")
            start = len(lines)
            lines.append(f"# {label}")
            # Every explanation line stays a comment, whatever the response body contains.
            lines.extend(f"# {line}" if line else "#" for line in explanation.splitlines())
            lines.append("#")
            commands = group.code_sample.splitlines()
            lines.extend(commands)
            output_calls += len(commands)
            test_cases.append(
                {
                    "id": group.case_id,
                    "name": f"{sent_as}: {group.failures[0].title}",
                    "filePath": file_name,
                    "startLine": start,
                    "endLine": len(lines) - 1,
                }
            )
    return ExportedFailures(
        script="\n".join(lines) + "\n",
        test_cases=test_cases,
        found_faults=found_faults,
        distinct_faults=len(distinct_faults),
        output_calls=output_calls,
    )


class WfcReportHandler(EventHandler["BaseExecutionContext"]):
    """Aggregate each scenario as it finishes and write a WFC Report at shutdown."""

    __slots__ = (
        "output",
        "started_at",
        "finished",
        "endpoint_ids",
        "covered",
        "failing_endpoints",
        "total_tests",
        "evaluated_calls",
    )

    def __init__(self, output: Path) -> None:
        self.output = output
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None
        self.endpoint_ids: set[str] = set()
        self.covered: dict[str, set[int | None]] = {}
        # Method and path each failing case was sent with, which differ from its operation for undeclared-method probes.
        self.failing_endpoints: dict[str, tuple[str, str]] = {}
        self.total_tests = 0
        self.evaluated_calls = 0

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            for path, item in event.schema.get("paths", {}).items():
                if isinstance(item, dict):
                    self.endpoint_ids.update(operation_id(method, path) for method in item if method in HTTP_METHODS)
        elif isinstance(event, EngineStarted):
            self.started_at = event.timestamp
        elif isinstance(event, EngineFinished):
            self.finished = event
        elif isinstance(event, ScenarioFinished):
            self._record(event.recorder)

    def _record(self, recorder: ScenarioRecorder) -> None:
        self.total_tests += len(recorder.cases)
        for case_id, interaction in recorder.interactions.items():
            self.evaluated_calls += 1
            endpoint = _case_operation_id(recorder.cases[case_id].value)
            # Requests to undeclared methods probe the API; they are not endpoints of it.
            if endpoint in self.endpoint_ids:
                status = interaction.response.status_code if interaction.response is not None else None
                self.covered.setdefault(endpoint, set()).add(status)
        for case_id, checks in recorder.checks.items():
            if any(check.failure_info is not None for check in checks):
                case = recorder.cases[case_id].value
                self.failing_endpoints[case_id] = (_case_operation_id(case), f"{case.method.upper()} {case.path}")

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        if self.finished is not None:
            elapsed = self.finished.running_time
        elif self.started_at is not None:
            elapsed = time.time() - self.started_at
        else:
            elapsed = 0.0
        script_path = self.output.with_suffix(".sh")
        exported = export_failures(ctx.statistic, self.failing_endpoints, ctx.config.output, script_path.name)
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "toolName": "Schemathesis",
            "toolVersion": SCHEMATHESIS_VERSION,
            "creationTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "faults": {
                "totalNumber": exported.distinct_faults,
                "foundFaults": exported.found_faults,
            },
            "problemDetails": {
                "rest": {
                    "outputHttpCalls": exported.output_calls,
                    "evaluatedHttpCalls": self.evaluated_calls,
                    "endpointIds": sorted(self.endpoint_ids),
                    "coveredHttpStatus": [
                        {
                            "endpointId": endpoint,
                            # One entry per operation: listing thousands of generated cases adds no information.
                            "testCaseId": endpoint,
                            "httpStatus": sorted(statuses, key=lambda status: (status is None, status or 0)),
                        }
                        for endpoint, statuses in sorted(self.covered.items())
                    ],
                }
            },
            "totalTests": self.total_tests,
            "testFilePaths": [script_path.name],
            "testCases": exported.test_cases,
            "executionTimeInSeconds": round(elapsed),
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(exported.script, encoding="utf-8")
        self.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
