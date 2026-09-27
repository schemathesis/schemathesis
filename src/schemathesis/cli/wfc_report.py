"""Web Fuzzing Commons (WFC) Report output."""

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.failures import AcceptedNegativeData, MalformedJson, ResponseTimeExceeded, ServerError
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine.events import EngineFinished, EngineStarted, ScenarioFinished
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
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
    from schemathesis.core.failures import Failure
    from schemathesis.engine import events
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case

SCHEMA_VERSION = "0.7.0"
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
# Codes 901-905 are Schemathesis-specific: WFC has no category for these oracles yet.
VALID_INPUT_REJECTED = 901
CREATED_RESOURCE_UNAVAILABLE = 902
SLOW_RESPONSE = 903
INVALID_INPUT_ACCEPTED = 904
UNMAPPED_AUTH_OR_METHOD = 905


def operation_id(method: str, path: str) -> str:
    return f"{method.upper()}:{path}"


def _case_operation_id(case: Case) -> str:
    return operation_id(case.method, case.path)


def fault_category(failure: Failure) -> tuple[int, str] | None:
    """WFC fault code and a description of the failure, or `None` when WFC has nothing for it."""
    description = f"{type(failure).__name__}: {failure.title}"
    if isinstance(failure, ServerError):
        return (205 if failure.status_code == 501 else 100), f"{description} (status {failure.status_code})"
    if isinstance(failure, VALIDATION_FAILURES):
        return 200, description
    if isinstance(failure, AllowHeaderMismatch):
        return 201, description
    if isinstance(failure, RejectedPositiveData):
        return (204 if failure.status_code == 406 else VALID_INPUT_REJECTED), description
    if isinstance(failure, (AcceptedNegativeData, MissingHeaderNotRejected)):
        return (206 if 200 <= failure.status_code < 300 else INVALID_INPUT_ACCEPTED), description
    if isinstance(failure, UnsupportedMethodResponse):
        if failure.failure_reason == "missing_allow_header":
            return 110, description
        if failure.failure_reason == "wrong_status" and 200 <= failure.status_code < 300:
            return 310, description
        return UNMAPPED_AUTH_OR_METHOD, description
    if isinstance(failure, UseAfterFree):
        return 113, description
    if isinstance(failure, IgnoredAuth):
        # WFC 308 covers unauthenticated modifications; unauthenticated reads have no WFC category.
        method = (failure.operation or "").split(" ", 1)[0].upper()
        return (308 if method in DESTRUCTIVE_METHODS else UNMAPPED_AUTH_OR_METHOD), description
    if isinstance(failure, EnsureResourceAvailability):
        return CREATED_RESOURCE_UNAVAILABLE, description
    if isinstance(failure, ResponseTimeExceeded):
        return SLOW_RESPONSE, description
    return None


class WfcReportHandler(EventHandler["BaseExecutionContext"]):
    """Aggregate each scenario as it finishes and write a WFC Report at shutdown."""

    __slots__ = (
        "output",
        "started_at",
        "finished",
        "endpoint_ids",
        "covered",
        "found_faults",
        "fault_keys",
        "test_cases",
        "total_tests",
        "evaluated_calls",
        "output_calls",
    )

    def __init__(self, output: Path) -> None:
        self.output = output
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None
        self.endpoint_ids: set[str] = set()
        self.covered: dict[str, set[int | None]] = {}
        self.found_faults: list[dict[str, object]] = []
        self.fault_keys: set[tuple[str, int, str]] = set()
        self.test_cases: dict[str, dict[str, str]] = {}
        self.total_tests = 0
        self.evaluated_calls = 0
        self.output_calls = 0

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
        # Checks are keyed by the case each failure belongs to, which always has a recorded response.
        for case_id, checks in recorder.checks.items():
            endpoint = _case_operation_id(recorder.cases[case_id].value)
            for check in checks:
                if check.failure_info is None:
                    continue
                failure = check.failure_info.failure
                category = fault_category(failure)
                if category is None:
                    continue
                code, description = category
                # WFC identifies a fault by code and context, so the context names the operation.
                context = f"{endpoint} -> {description}"
                key = (case_id, code, context)
                if key in self.fault_keys:
                    continue
                self.fault_keys.add(key)
                if case_id not in self.test_cases:
                    self.test_cases[case_id] = {"id": case_id, "name": failure.title}
                    self.output_calls += 1
                self.found_faults.append(
                    {
                        "operationId": endpoint,
                        "testCaseId": case_id,
                        "faultCategories": [{"code": code, "context": context}],
                    }
                )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        if self.finished is not None:
            elapsed = self.finished.running_time
        elif self.started_at is not None:
            elapsed = time.time() - self.started_at
        else:
            elapsed = 0.0
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "toolName": "Schemathesis",
            "toolVersion": SCHEMATHESIS_VERSION,
            "creationTime": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "faults": {
                "totalNumber": len({(code, context) for _, code, context in self.fault_keys}),
                "foundFaults": self.found_faults,
            },
            "problemDetails": {
                "rest": {
                    "outputHttpCalls": self.output_calls,
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
            "testFilePaths": [],
            "testCases": list(self.test_cases.values()),
            "executionTimeInSeconds": round(elapsed),
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
