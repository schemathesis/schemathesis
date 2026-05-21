from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import requests

from schemathesis.config import SanitizationConfig
from schemathesis.core import NOT_SET
from schemathesis.core.failures import Failure
from schemathesis.core.parameters import LOCATION_TO_CONTAINER
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.stateful.state_machine import ExtractedParam, Transition
from schemathesis.openapi.checks import UseAfterFree
from schemathesis.reporting.crashes import CrashFile, build_crashes_from_recorder

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class FailingCheck:
    name: str
    message: str = "boom"
    code_sample: str = "curl http://127.0.0.1"
    # Sibling step this failure references (e.g. the freeing DELETE), recorded so the sequence splices it in.
    related_step: int | None = None


@dataclass
class LinkParameter:
    location: str
    name: str
    expression: str


@dataclass
class Link:
    operation_id: str
    parameters: list[LinkParameter] = field(default_factory=list)
    request_body: Any = None


@dataclass
class Step:
    method: str
    path: str
    status: int
    body: bytes = b"{}"
    content_type: str | None = "application/json"
    response_headers: dict[str, list[str]] = field(default_factory=dict)
    request_url: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    path_parameters: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    cookies: dict[str, Any] = field(default_factory=dict)
    case_body: Any = NOT_SET
    media_type: str | None = None
    link: Link | None = None
    checks: list[FailingCheck] = field(default_factory=list)
    # Index of the step this one descends from; defaults to the previous step (a linear chain).
    parent: int | None = None


def _build_transition(*, link: Link, parent_id: str) -> Transition:
    parameters: dict[str, dict[str, ExtractedParam]] = {}
    for parameter in link.parameters:
        container = LOCATION_TO_CONTAINER[parameter.location]
        extracted = ExtractedParam(definition=parameter.expression, value=Ok(None), is_required=True)
        parameters.setdefault(container, {})[parameter.name] = extracted
    request_body = (
        ExtractedParam(definition=link.request_body, value=Ok(None), is_required=True)
        if link.request_body is not None
        else None
    )
    return Transition(
        id=link.operation_id,
        parent_id=parent_id,
        is_inferred=False,
        parameters=parameters,
        request_body=request_body,
    )


@pytest.fixture
def crash_factory(ctx):

    def operation_for(method: str, path: str):
        schema = ctx.openapi.load_schema({path: {method.lower(): {"responses": {"200": {"description": "OK"}}}}})
        return schema[path][method.upper()]

    def record_step(
        recorder: ScenarioRecorder,
        *,
        step: Step,
        parent_id: str | None,
        case_ids: list[str],
    ) -> str:
        operation = operation_for(step.method, step.path)
        case = operation.Case(
            method=step.method.upper(),
            path_parameters=step.path_parameters,
            query=step.query,
            cookies=step.cookies,
            body=step.case_body,
            media_type=step.media_type,
        )
        transition = _build_transition(link=step.link, parent_id=parent_id) if step.link is not None else None
        recorder.record_case(
            parent_id=parent_id,
            case=case,
            transition=transition,
            is_transition_applied=transition is not None,
        )

        request_url = step.request_url or f"http://127.0.0.1{step.path}"
        prepared = requests.Request(method=step.method.upper(), url=request_url, headers=step.request_headers).prepare()
        headers = dict(step.response_headers)
        if step.content_type:
            headers.setdefault("content-type", [step.content_type])
        recorder.record_response(
            case_id=case.id,
            response=Response(
                status_code=step.status,
                headers=headers,
                content=step.body,
                request=prepared,
                elapsed=0.1,
                verify=False,
            ),
        )
        for check in step.checks:
            if check.related_step is not None:
                failure: Failure = UseAfterFree(
                    operation=operation.label,
                    message=check.message,
                    free="DELETE",
                    usage="GET",
                    deleted_case_id=case_ids[check.related_step],
                )
            else:
                failure = Failure(operation=operation.label, title="Server error", message=check.message)
            recorder.record_check_failure(
                name=check.name, case_id=case.id, code_sample=check.code_sample, failure=failure
            )
        return case.id

    def build(*, steps: list[Step], label: str, code_sample: str = "") -> CrashFile:
        recorder = ScenarioRecorder(label=label)
        case_ids: list[str] = []
        for index, step in enumerate(steps):
            if step.parent is not None:
                parent_id: str | None = case_ids[step.parent]
            elif index > 0:
                parent_id = case_ids[-1]
            else:
                parent_id = None
            case_ids.append(record_step(recorder, step=step, parent_id=parent_id, case_ids=case_ids))
        terminal_id = case_ids[-1]

        # Disabled so recorded case values flow through verbatim, unchanged by output sanitization.
        crashes = build_crashes_from_recorder(
            recorder=recorder, failing_case_id=terminal_id, sanitization=SanitizationConfig(enabled=False)
        )
        assert len(crashes) == 1, f"Expected exactly one crash, got {len(crashes)}"
        crash = crashes[0]
        if code_sample:
            crash.code_sample = code_sample
        return crash

    return CrashFactory(build=build)


@dataclass
class CrashFactory:
    build: Callable[..., CrashFile]

    def single(
        self,
        *,
        method: str = "GET",
        path: str,
        status: int,
        body: bytes = b"{}",
        content_type: str | None = "application/json",
        response_headers: dict[str, list[str]] | None = None,
        checks: list[FailingCheck] | None = None,
        code_sample: str = "",
        request_headers: dict[str, str] | None = None,
        request_url: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        case_body: Any = NOT_SET,
        media_type: str | None = None,
    ) -> CrashFile:
        step = Step(
            method=method,
            path=path,
            status=status,
            body=body,
            content_type=content_type,
            response_headers=response_headers or {},
            request_url=request_url,
            request_headers=request_headers or {},
            path_parameters=path_parameters or {},
            query=query or {},
            cookies=cookies or {},
            case_body=case_body,
            media_type=media_type,
            checks=checks or [FailingCheck(name="not_a_server_error")],
        )
        return self.build(steps=[step], label=f"{method.upper()} {path}", code_sample=code_sample)

    def chain(self, *, steps: list[Step]) -> CrashFile:
        label = " -> ".join(f"{step.method.upper()} {step.path}" for step in steps)
        return self.build(steps=steps, label=label)
