"""Failures of a run as a standalone pytest file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.failures import Failure, MalformedJson, ResponseTimeExceeded
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine.events import FuzzScenarioFinished, ScenarioFinished
from schemathesis.openapi.checks import (
    JsonSchemaError,
    MalformedMediaType,
    MissingContentType,
    MissingHeaders,
    UndefinedContentType,
)

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.core.transport import Response
    from schemathesis.engine import events
    from schemathesis.engine.recorder import Interaction, ReproductionStep
    from schemathesis.engine.statistic import Statistic
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def _render_request(interaction: Interaction, operation: APIOperation, config: OutputConfig) -> list[str]:
    request = interaction.request
    headers = {name: ", ".join(values) for name, values in request.headers.items()}
    uri = request.uri
    if config.sanitization.enabled:
        uri = sanitize_url(uri, config=config.sanitization)
        sanitize_value(headers, config=config.sanitization)
    lines = ["    response = requests.request(", f"        {request.method!r},", f"        {uri!r},"]
    lines.append(f"        headers={headers!r},")
    if request.body is not None:
        lines.append(f"        data={request.body!r},")
    if interaction.response is not None and interaction.response.verify is not True:
        lines.append(f"        verify={interaction.response.verify!r},")
    settings = operation.schema.config.request_settings_for(operation=operation)
    if settings.cert is not None:
        lines.append(f"        cert={settings.cert!r},")
    proxy = settings.proxy
    if proxy is not None:
        if config.sanitization.enabled:
            proxy = sanitize_url(proxy, config=config.sanitization)
        lines.append(f"        proxies={{'all': {proxy!r}}},")
    if settings.timeout is not None:
        lines.append(f"        timeout={settings.timeout!r},")
    lines.append("    )")
    assert interaction.response is not None
    lines.append(f"    assert response.status_code == {interaction.response.status_code}")
    return lines


_RAISES_ON_JSON = "    with pytest.raises(ValueError):\n        response.json()"


def _symptom(failure: Failure, response: Response) -> list[str]:
    if isinstance(failure, MissingContentType):
        return ["    assert 'Content-Type' not in response.headers"]
    if isinstance(failure, MissingHeaders):
        return [f"    assert {name!r} not in response.headers" for name in failure.missing_headers]
    if isinstance(failure, UndefinedContentType):
        return [f"    assert response.headers['Content-Type'] == {failure.content_type!r}"]
    # A malformed media type in the schema itself is not a property of the response.
    if isinstance(failure, MalformedMediaType) and failure.actual != failure.defined:
        return [f"    assert response.headers['Content-Type'] == {failure.actual!r}"]
    if isinstance(failure, ResponseTimeExceeded):
        return [f"    assert response.elapsed.total_seconds() > {failure.deadline!r}"]
    if isinstance(failure, (MalformedJson, JsonSchemaError)):
        # Only symptoms that hold for the recorded body: Python's parser accepts some bodies the check rejects,
        # and schema violations in headers or non-JSON bodies have no place in `response.json()`.
        try:
            value = json.loads(response.content)
        except ValueError:
            if isinstance(failure, MalformedJson):
                return [_RAISES_ON_JSON]
            return []
        if isinstance(failure, JsonSchemaError):
            for key in failure.instance_path:
                try:
                    value = value[key]
                except (KeyError, IndexError, TypeError):
                    return []
            if value == failure.instance:
                path = "".join(f"[{key!r}]" for key in failure.instance_path)
                return [f"    assert response.json(){path} == {failure.instance!r}"]
    return []


def _comment(text: str) -> list[str]:
    # Python rejects NUL bytes and lone surrogates anywhere in a source file.
    text = text.replace("\x00", "\\x00").encode("utf-8", "backslashreplace").decode("utf-8")
    return [f"# {line}" if line else "#" for line in text.splitlines()]


def _preconditions(case: Case, replayed: set[str]) -> list[str]:
    """Objects `case` takes from earlier responses whose requests the test does not send."""
    lines: list[str] = []
    for draw in case.meta.pool_draws if case.meta is not None else ():
        if draw.source_operation not in replayed:
            text = f"Needs the `{draw.resource_name}` that `{draw.source_operation}` returned"
            if draw.source_identity is not None:
                text += f" to `{draw.source_identity}`"
            lines.extend(f"    {line}" for line in _comment(text))
    return lines


def _render_steps(
    steps: list[ReproductionStep], interactions: dict[str, Interaction], cases: dict[str, Case], config: OutputConfig
) -> list[str]:
    lines: list[str] = []
    replayed: set[str] = set()
    for step in steps:
        case = cases[step.case_id]
        lines.extend(line for line in _preconditions(case, replayed) if line not in lines)
        # Credentials are masked, so the identity tells which ones each request needs.
        if step.sender is not None:
            lines.extend(f"    {line}" for line in _comment(f"as {step.sender}"))
        lines.extend(_render_request(interactions[step.case_id], case.operation, config))
        replayed.add(step.operation)
    return lines


def render(
    statistic: Statistic,
    interactions: dict[str, Interaction],
    cases: dict[str, Case],
    config: OutputConfig,
    location: str | None,
) -> str:
    if location and config.sanitization.enabled:
        location = sanitize_url(location, config=config.sanitization)
    lines = _comment(f"Generated by Schemathesis {SCHEMATHESIS_VERSION}" + (f" from {location}" if location else ""))
    lines.append("# Each test asserts the status code and failure symptoms the run saw, so it fails once they change.")
    if config.sanitization.enabled:
        lines.append(
            f"# Sensitive values are replaced with `{config.sanitization.replacement}`; fill them in before running."
        )
    lines.append("import requests")
    imports = len(lines)
    for label in sorted(statistic.failures):
        for index, group in enumerate(statistic.failures[label].values(), 1):
            if group.case_id is None:
                continue
            steps = [step for step in group.steps if step.case_id in interactions]
            explanation = group.format(index=index, curl=None, config=config)
            # Non-ASCII word characters are not always valid in identifiers.
            slug = re.sub(r"\W+", "_", group.steps[-1].operation, flags=re.ASCII).strip("_").lower()
            lines.extend(["", ""])
            # Every comment line stays a comment, whatever the schema or the response body contains.
            lines.extend(_comment(label))
            lines.extend(_comment(explanation))
            lines.append(f"def test_{slug}_{group.case_id}():")
            lines.extend(_render_steps(steps, interactions, cases, config))
            response = interactions[steps[-1].case_id].response
            assert response is not None
            symptoms: list[str] = []
            for failure in group.failures:
                for line in _symptom(failure, response):
                    if line not in symptoms:
                        symptoms.append(line)
            lines.extend(symptoms)
    if _RAISES_ON_JSON in lines:
        lines.insert(imports, "import pytest")
    return "\n".join(lines) + "\n"


class PytestReportHandler(EventHandler["BaseExecutionContext"]):
    """Keep the requests of failing scenarios and write them as pytest tests at shutdown."""

    __slots__ = ("output", "location", "interactions", "cases")

    def __init__(self, output: Path) -> None:
        self.output = output
        self.location: str | None = None
        self.interactions: dict[str, Interaction] = {}
        self.cases: dict[str, Case] = {}

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self.location = event.location
        elif isinstance(event, (ScenarioFinished, FuzzScenarioFinished)):
            recorder = event.recorder
            for checks in recorder.checks.values():
                for check in checks:
                    if check.failure_info is None:
                        continue
                    for step in check.failure_info.steps:
                        self.interactions[step.case_id] = recorder.interactions[step.case_id]
                        self.cases[step.case_id] = recorder.cases[step.case_id].value

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            render(ctx.statistic, self.interactions, self.cases, ctx.config.output, self.location),
            encoding="utf-8",
        )
