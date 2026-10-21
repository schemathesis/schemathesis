from __future__ import annotations

import json
from collections import Counter
from collections.abc import MutableMapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from schemathesis.core import NOT_SET
from schemathesis.core.curl import get_excluded_headers
from schemathesis.core.media_types import is_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import UNRESOLVABLE, iter_decoded_pointer_segments
from schemathesis.core.transport import AUTH_REJECTION_STATUSES
from schemathesis.reporting.crashes import CrashCheck, CrashFile, CrashLink, CrashStep

if TYPE_CHECKING:
    import requests

    from schemathesis.checks import CheckContext
    from schemathesis.config import ProjectConfig
    from schemathesis.core.failures import Failure
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.schemas import APIOperation, BaseSchema
    from schemathesis.wfc.escalation import EscalatingAuthProvider


class ReplayStatus(Enum):
    FIXED = "fixed"
    FAILED = "failed"
    FLAKY = "flaky"
    ERRORED = "errored"


# A single passing replay does not prove a fix; an intermittent failure needs more attempts to show itself.
MAX_REPLAY_ATTEMPTS = 3


@dataclass(slots=True)
class LinkedValue:
    """A value a step's link re-extracted from its parent's live response."""

    location: str
    name: str
    # `NOT_SET` when the recorded request had no value there.
    recorded: object
    # `UNRESOLVABLE` when the live response lacks it, so the recorded value was sent instead.
    fresh: object
    # JSON Pointers into the parent's response body the link extracts the value from.
    pointers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StepOutcome:
    status_code: int
    body: str
    # The command reproducing the request this replay sent for the step.
    curl: str = ""
    links: list[LinkedValue] = field(default_factory=list)


@dataclass(slots=True)
class RejectedStep:
    """A replayed step the API rejected for authentication though the recorded request was accepted."""

    number: int
    status_code: int
    recorded_status_code: int


@dataclass(slots=True)
class CheckOutcome:
    name: str
    status: ReplayStatus
    note: str = ""


@dataclass(slots=True)
class ReplayOutcome:
    status: ReplayStatus
    duration_ms: int = 0
    error_message: str = ""
    step_outcomes: list[StepOutcome] = field(default_factory=list)
    check_outcomes: list[CheckOutcome] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    transport_response: Response | None = None
    # Commands reproducing the requests this replay sent; empty when it stopped before the terminal step.
    code_sample: str = ""
    unsupplied_masked: list[tuple[ParameterLocation, str]] = field(default_factory=list)
    rejected: RejectedStep | None = None


def replay_crash_file(
    crash: CrashFile,
    *,
    base_url: str | None = None,
    project_config: ProjectConfig,
    schema: BaseSchema,
) -> ReplayOutcome:
    from schemathesis.engine.context import make_session

    operation = _resolve_operation(schema, crash)
    # A fresh session per crash: applies the operation's auth/headers/TLS config and isolates cookie state.
    session = make_session(project_config, operation=operation)
    try:
        return _replay_with_retries(crash, base_url=base_url, session=session, schema=schema, operation=operation)
    finally:
        session.close()


def _replay_with_retries(
    crash: CrashFile,
    *,
    base_url: str | None,
    session: requests.Session,
    schema: BaseSchema,
    operation: APIOperation | None,
) -> ReplayOutcome:
    """Replay until every check has failed at least once, or the attempt budget runs out."""
    failure_counts: Counter[str] = Counter()
    first_failing: ReplayOutcome | None = None
    outcome: ReplayOutcome | None = None
    rejected_attempt: ReplayOutcome | None = None
    attempts = 0

    for _ in range(MAX_REPLAY_ATTEMPTS):
        attempt = _replay_sequence(
            crash.sequence, base_url=base_url, session=session, schema=schema, operation=operation
        )
        if not attempt.check_outcomes:
            # The replay aborted before it could evaluate anything; further attempts would abort the same way.
            return attempt
        if attempt.rejected is not None:
            # An unauthenticated attempt says nothing about the fix, so it never counts towards the verdict.
            rejected_attempt = attempt
            continue
        outcome = attempt
        attempts += 1
        failed = [check.name for check in outcome.check_outcomes if check.status is ReplayStatus.FAILED]
        failure_counts.update(failed)
        if failed and first_failing is None:
            first_failing = outcome
        if all(
            failure_counts[check.name] for check in outcome.check_outcomes if check.status is not ReplayStatus.ERRORED
        ):
            break

    if outcome is None:
        assert rejected_attempt is not None
        # Every attempt was rejected: checks that still failed stand, passing ones cannot count as fixed.
        reported = rejected_attempt
        check_outcomes = rejected_attempt.check_outcomes
    else:
        # Report the attempt that failed, so its response and failures are the ones shown.
        reported = first_failing or outcome
        check_outcomes = [
            _final_check_outcome(check, failure_counts[check.name], attempts) for check in reported.check_outcomes
        ]
    rejected = rejected_attempt.rejected if rejected_attempt is not None else None
    return _unverified_without_recorded_values(
        replace(reported, status=_case_status(check_outcomes), check_outcomes=check_outcomes, rejected=rejected),
        unrestorable=has_unrestorable_masked_values(crash, schema.config.output.sanitization.replacement),
    )


def _unverified_without_recorded_values(outcome: ReplayOutcome, *, unrestorable: bool) -> ReplayOutcome:
    """A pass without the values the recorded request carried proves nothing, so it never counts as fixed."""
    if outcome.unsupplied_masked:
        message = _missing_credentials_message(outcome.unsupplied_masked)
        note = f"{_masked_names(outcome.unsupplied_masked)} masked in the crash file"
    elif outcome.rejected is not None:
        message = _rejected_credentials_message(outcome.rejected, steps=len(outcome.step_outcomes))
        note = f"replay was not authenticated ({outcome.rejected.status_code})"
    elif unrestorable:
        message = (
            "the crash file has masked values that replay cannot restore; "
            "record it with `output.sanitization.enabled = false` to replay it"
        )
        note = "masked values in the crash file"
    else:
        return outcome
    if outcome.status is ReplayStatus.FIXED:
        return _errored_outcome(message, elapsed=outcome.duration_ms, step_outcomes=outcome.step_outcomes)
    check_outcomes = [
        CheckOutcome(name=check.name, status=ReplayStatus.ERRORED, note=note)
        if check.status is ReplayStatus.FIXED
        else check
        for check in outcome.check_outcomes
    ]
    return replace(outcome, status=_case_status(check_outcomes), check_outcomes=check_outcomes)


def _final_check_outcome(check: CheckOutcome, failure_count: int, attempts: int) -> CheckOutcome:
    if check.status is ReplayStatus.ERRORED:
        return check
    if failure_count == 0:
        return CheckOutcome(name=check.name, status=ReplayStatus.FIXED)
    if failure_count == attempts:
        return CheckOutcome(name=check.name, status=ReplayStatus.FAILED)
    return CheckOutcome(
        name=check.name,
        status=ReplayStatus.FLAKY,
        note=f"passed {attempts - failure_count} of {attempts} replays",
    )


def _errored_outcome(
    error_message: str, *, elapsed: int, step_outcomes: list[StepOutcome] | None = None
) -> ReplayOutcome:
    return ReplayOutcome(
        status=ReplayStatus.ERRORED,
        duration_ms=elapsed,
        error_message=error_message,
        step_outcomes=step_outcomes or [],
    )


def _step_operation(schema: BaseSchema, step: CrashStep) -> APIOperation | None:
    if step.operation is not None:
        operation = schema.find_operation_by_label(step.operation)
        if operation is not None:
            return operation
    try:
        return schema[step.path][step.method]
    except LookupError:
        return None


def _replay_sequence(
    sequence: list[CrashStep],
    *,
    base_url: str | None,
    session: requests.Session,
    schema: BaseSchema,
    operation: APIOperation | None,
) -> ReplayOutcome:
    from schemathesis.auths import AuthContext, set_on_case
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.wfc.escalation import escalating_provider

    step_outcomes: list[StepOutcome] = []
    instant = Instant()

    # Record the replayed steps so history-dependent checks see the same sequence, each linked to its parent.
    recorder = ScenarioRecorder(label="replay", config=schema.config.output)
    step_outputs: list[StepOutput] = []

    terminal_response: Response | None = None
    last_index = len(sequence) - 1
    replacement = schema.config.output.sanitization.replacement
    # Masked values no credential source replaced, so the replay sent the placeholder instead.
    unsupplied: list[tuple[ParameterLocation, str]] = []
    # The first step the API rejected for authentication where the recorded request was accepted.
    rejected: RejectedStep | None = None

    identities = escalating_provider(schema)
    for index, step in enumerate(sequence):
        if step.auth_identity is not None and (identities is None or step.auth_identity not in identities.names):
            # Sending it as anyone else would replay a different scenario.
            return _errored_outcome(
                _unknown_identity_message(step.auth_identity, number=index + 1, steps=len(sequence)),
                elapsed=instant.elapsed_ms,
            )

    for index, step in enumerate(sequence):
        step_operation = operation if index == last_index else _step_operation(schema, step)
        if step_operation is None:
            return _errored_outcome(
                f"operation not found in schema at step {index + 1}",
                elapsed=instant.elapsed_ms,
                step_outcomes=step_outcomes,
            )

        try:
            case = _build_case(step_operation, step)
        except Exception:
            # The recorded data (typically `meta`) can't be decoded - a corrupt file or a different version.
            return _errored_outcome(
                f"step {index + 1}: incompatible or corrupted crash data",
                elapsed=instant.elapsed_ms,
                step_outcomes=step_outcomes,
            )

        # Credentials come from the current config, CLI options and auth providers, as in a run.
        set_on_case(case, AuthContext(operation=case.operation, app=case.operation.app), None)
        # Each step goes out as the identity that sent it, so a peer's request is not replayed as the owner.
        if (
            identities is not None
            and step.auth_identity is not None
            and case._auth_identity not in (None, step.auth_identity)
        ):
            _send_as(case, identities, step.auth_identity)

        # A link extracts from its recorded parent step, not always the previous one (older crashes lack it).
        parent_output: StepOutput | None = None
        if step.parent_index is not None:
            if step.parent_index < len(step_outputs):
                parent_output = step_outputs[step.parent_index]
        elif step_outputs:
            parent_output = step_outputs[-1]

        links: list[LinkedValue] = []
        if (
            parent_output is not None
            and step.link is not None
            and (step.link.parameters or step.link.request_body is not None)
        ):
            try:
                links = _apply_link_parameters(case, step.link, parent_output)
            except (KeyError, ValueError) as exc:
                return _errored_outcome(
                    f"extraction failed at step {index + 1} - {exc}",
                    elapsed=instant.elapsed_ms,
                    step_outcomes=step_outcomes,
                )

        try:
            response = case.call(base_url=base_url, session=session)
        except Exception as exc:
            return _errored_outcome(str(exc), elapsed=instant.elapsed_ms, step_outcomes=step_outcomes)

        if (
            rejected is None
            and response.status_code in AUTH_REJECTION_STATUSES
            and step.response_status not in AUTH_REJECTION_STATUSES
        ):
            rejected = RejectedStep(
                number=index + 1, status_code=response.status_code, recorded_status_code=step.response_status
            )

        for parameter in _masked_parameters(step, replacement):
            if parameter not in unsupplied and _sent_value(response.request, *parameter) in (None, replacement):
                unsupplied.append(parameter)

        parent_id = parent_output.case.id if parent_output is not None else None
        recorder.record_case(parent_id=parent_id, case=case)
        recorder.record_response(case_id=case.id, response=response)

        # Built from the headers this step actually sent, the same way a run reports its failures.
        curl = case.as_curl_command(headers=recorder.find_request_headers(case_id=case.id), verify=response.verify)
        step_outcomes.append(
            StepOutcome(status_code=response.status_code, body=response.text_lossy(), curl=curl, links=links)
        )
        step_outputs.append(StepOutput(response=response, case=case))
        if index == last_index:
            terminal_response = response

    elapsed = instant.elapsed_ms
    assert terminal_response is not None
    terminal = sequence[-1]
    checked = step_outputs[_checked_index(sequence)]
    try:
        check_outcomes, check_failures = _evaluate_checks(
            case=checked.case,
            response=checked.response,
            recorded_checks=terminal.checks,
            recorder=recorder,
        )
    except Exception as exc:
        return _errored_outcome(f"check evaluation failed - {exc}", elapsed=elapsed, step_outcomes=step_outcomes)
    if not check_outcomes:
        return _errored_outcome("recorded no checks to verify", elapsed=elapsed, step_outcomes=step_outcomes)
    status = _case_status(check_outcomes)

    return ReplayOutcome(
        status=status,
        duration_ms=elapsed,
        step_outcomes=step_outcomes,
        check_outcomes=check_outcomes,
        failures=check_failures,
        transport_response=terminal_response,
        code_sample="\n".join(outcome.curl for outcome in step_outcomes),
        unsupplied_masked=unsupplied,
        rejected=rejected,
    )


def _checked_index(sequence: list[CrashStep]) -> int:
    """The step the recorded checks ran on."""
    terminal = sequence[-1]
    parent = terminal.parent_index
    # Sent as another identity than its parent without a link: a peer's request a check sent from the parent,
    # so the checks run on the parent and send it again.
    if (
        terminal.link is None
        and parent is not None
        and terminal.auth_identity is not None
        and sequence[parent].auth_identity not in (None, terminal.auth_identity)
    ):
        return parent
    return len(sequence) - 1


def _send_as(case: Case, identities: EscalatingAuthProvider, name: str) -> None:
    """Replace the credentials on `case` with the ones `name` sends."""
    from schemathesis.specs.openapi.object_authorization import apply_as

    sent = apply_as(case, identities.provider_for(name), name)
    case.headers, case.query, case.cookies = sent.headers, sent.query, sent.cookies
    case._auth_identity = name


def _apply_link_parameters(case: Case, link: CrashLink, previous_step_output: StepOutput) -> list[LinkedValue]:
    from schemathesis.specs.openapi.expressions import body_pointers, evaluate

    linked: list[LinkedValue] = []
    # Re-extract each link parameter from the previous response into its container.
    for key, expression in link.parameters.items():
        location, _, name = key.partition(".")
        container = case.get_container(ParameterLocation(location))
        assert isinstance(container, MutableMapping)
        value = evaluate(expression, previous_step_output)
        linked.append(
            LinkedValue(
                location=location,
                name=name,
                recorded=container.get(name, NOT_SET),
                fresh=value,
                pointers=body_pointers(expression),
            )
        )
        if value is UNRESOLVABLE:
            # Re-extraction failed; reuse the value captured at the original failure.
            continue
        container[name] = value

    if link.request_body is not None:
        # Re-extract the body from the previous response, merging into the recorded one.
        body = evaluate(link.request_body, previous_step_output, evaluate_nested=True)
        if isinstance(body, dict) and isinstance(case.body, dict) and isinstance(link.request_body, dict):
            recorded_body = case.body
            linked.extend(
                LinkedValue(
                    location="body",
                    name=name,
                    recorded=recorded_body.get(name, NOT_SET),
                    fresh=value,
                    pointers=body_pointers(link.request_body.get(name)),
                )
                for name, value in body.items()
            )
            case.body = {**recorded_body, **body}
        else:
            linked.append(
                LinkedValue(
                    location="body",
                    name="body",
                    recorded=case.body,
                    fresh=body,
                    pointers=body_pointers(link.request_body),
                )
            )
            if body is not UNRESOLVABLE:
                case.body = body
    return linked


def _evaluate_checks(
    *,
    case: Case,
    response: Response,
    recorded_checks: list[CrashCheck],
    recorder: ScenarioRecorder,
) -> tuple[list[CheckOutcome], list[Failure]]:
    from schemathesis.checks import CHECKS, is_check_class, load_all_checks, run_checks

    # Re-run exactly the recorded checks, ignoring the current enabled set; class-based ones report as unavailable.
    load_all_checks()
    registry = {check.__name__: check for check in CHECKS.get_all() if not is_check_class(check)}
    check_context = _make_check_context(case.operation, recorder)

    runnable = {check.name: registry[check.name] for check in recorded_checks if check.name in registry}

    failed_check_names: set[str] = set()
    all_failures: list[Failure] = []

    def on_failure(name: str, collected: set, failure: Failure) -> None:
        failed_check_names.add(name)
        all_failures.append(failure)

    run_checks(
        case=case,
        response=response,
        ctx=check_context,
        checks=list(runnable.values()),
        on_failure=on_failure,
    )

    check_outcomes: list[CheckOutcome] = []
    for check in recorded_checks:
        if check.name not in runnable:
            check_outcomes.append(
                CheckOutcome(name=check.name, status=ReplayStatus.ERRORED, note="check not available to re-run")
            )
        elif check.name in failed_check_names:
            check_outcomes.append(CheckOutcome(name=check.name, status=ReplayStatus.FAILED))
        else:
            check_outcomes.append(CheckOutcome(name=check.name, status=ReplayStatus.FIXED))

    # Order failures by severity so replay output matches the run.
    return check_outcomes, sorted(set(all_failures))


def _make_check_context(operation: APIOperation, recorder: ScenarioRecorder) -> CheckContext:
    from schemathesis.checks import CheckContext

    return CheckContext(
        override=None,
        auth=None,
        headers=None,
        config=operation.schema.config.checks_config_for(operation=operation),
        transport_kwargs=None,
        recorder=recorder,
        response_checks=None,
    )


def _resolve_operation(schema: BaseSchema, crash: CrashFile) -> APIOperation | None:
    operation = schema.find_operation_by_label(crash.operation)
    if operation is not None:
        return operation
    try:
        path_map = schema[crash.path_template]
    except LookupError:
        return None
    try:
        return path_map[crash.method]
    except LookupError:
        # No declared operation for this method; borrow any on the path and replay the recorded method.
        return next(iter(path_map.values()), None)


# Headers the transport recomputes; replaying recorded values would misframe the request.
_STALE_HEADERS = frozenset({"content-length", "host", "transfer-encoding", "connection", "content-encoding", "cookie"})


def _build_case(operation: APIOperation, step: CrashStep) -> Case:
    from schemathesis.generation.meta import CaseMetadata

    # Wire headers carry user-supplied ones such as `-H`; the transport regenerates the rest.
    excluded = get_excluded_headers()
    case_header_names = {key.lower() for key in step.case_headers}
    headers = {
        key: value
        for key, value in step.request_headers.items()
        if key.lower() not in _STALE_HEADERS and key not in excluded and key.lower() not in case_header_names
    }
    headers.update(step.case_headers)
    case = operation.Case(
        method=step.method,
        path_parameters=step.path_parameters,
        query=step.query,
        headers=headers,
        cookies=step.cookies,
        body=step.case_body,
        media_type=step.media_type,
    )
    if step.meta is not None:
        case._meta = CaseMetadata.from_dict(step.meta)
    case.freeze_metadata()
    return case


def _is_masked(value: object, replacement: str) -> bool:
    return value == replacement or value == [replacement]


def _masked_parameters(step: CrashStep, replacement: str) -> list[tuple[ParameterLocation, str]]:
    """Sent parameters whose whole value was masked; the current credentials may replace them."""
    return [
        *(
            (ParameterLocation.HEADER, name)
            for name, value in step.request_headers.items()
            if _is_masked(value, replacement)
        ),
        *((ParameterLocation.QUERY, name) for name, value in step.query.items() if _is_masked(value, replacement)),
        *((ParameterLocation.COOKIE, name) for name, value in step.cookies.items() if _is_masked(value, replacement)),
    ]


def _sent_value(request: requests.PreparedRequest, location: ParameterLocation, name: str) -> str | None:
    if location is ParameterLocation.HEADER:
        return request.headers.get(name)
    if location is ParameterLocation.QUERY:
        values = parse_qs(urlsplit(request.url or "").query, keep_blank_values=True).get(name)
        return values[0] if values else None
    pairs = (pair.split("=", 1) for pair in request.headers.get("Cookie", "").split(";"))
    return next((pair[1].strip() for pair in pairs if len(pair) == 2 and pair[0].strip() == name), None)


_LOCATION_LABELS = {
    ParameterLocation.HEADER: "header",
    ParameterLocation.QUERY: "query parameter",
    ParameterLocation.COOKIE: "cookie",
}


def _masked_names(masked: list[tuple[ParameterLocation, str]]) -> str:
    return ", ".join(f"`{name}` {_LOCATION_LABELS[location]}" for location, name in masked)


def _missing_credentials_message(missing: list[tuple[ParameterLocation, str]]) -> str:
    names = _masked_names(missing)
    if len(missing) == 1:
        return f"{names} was masked in the crash file; provide it via config, -H, --auth or an auth hook"
    return f"{names} were masked in the crash file; provide them via config, -H, --auth or an auth hook"


def _rejected_credentials_message(rejected: RejectedStep, *, steps: int) -> str:
    location = f" at step {rejected.number}" if steps > 1 else ""
    return (
        f"replay was not authenticated: the API answered {rejected.status_code}{location} "
        f"where the recorded request got {rejected.recorded_status_code}; "
        "check the credentials passed via config, -H, --auth or an auth hook"
    )


def _unknown_identity_message(name: str, *, number: int, steps: int) -> str:
    location = f"step {number} was" if steps > 1 else "the request was"
    return (
        f"{location} sent as `{name}`, but replay cannot send requests as `{name}`; "
        "pass the WFC auth document the run used via `--auth-wfc` or `[auth.wfc]`"
    )


def has_unrestorable_masked_values(crash: CrashFile, replacement: str) -> bool:
    """Whether a stored request has masked values no credential source can replace, so a replay is not faithful."""
    for step in crash.sequence:
        for container in (step.request_headers, step.case_headers, step.query, step.cookies):
            if any(replacement in str(value) and not _is_masked(value, replacement) for value in container.values()):
                return True
        if any(replacement in str(value) for value in step.path_parameters.values()):
            return True
        if replacement in str(step.case_body):
            return True
    return False


def _case_status(check_outcomes: list[CheckOutcome]) -> ReplayStatus:
    if any(c.status is ReplayStatus.FAILED for c in check_outcomes):
        return ReplayStatus.FAILED
    if any(c.status is ReplayStatus.FLAKY for c in check_outcomes):
        return ReplayStatus.FLAKY
    if any(c.status is ReplayStatus.ERRORED for c in check_outcomes):
        return ReplayStatus.ERRORED
    return ReplayStatus.FIXED


def bodies_equal(
    recorded: str,
    actual: str,
    *,
    content_type: str,
    masked_pointers: list[str] | None = None,
    linked: list[LinkedValue] | None = None,
) -> bool:
    """Whether a replayed body matches the recorded one, ignoring values links re-extracted.

    `masked_pointers` locate values later steps extract from; `linked` values are ignored where their links extract them.
    """
    if recorded == actual:
        return True
    if content_type and is_json(content_type):
        try:
            recorded_document, actual_document = json.loads(recorded), json.loads(actual)
        except (ValueError, TypeError):
            return False
        for pointer in masked_pointers or ():
            recorded_document, actual_document = _mask(
                recorded_document, actual_document, list(iter_decoded_pointer_segments(pointer))
            )
        for value in linked or ():
            if value.fresh is UNRESOLVABLE:
                continue
            for pointer in value.pointers:
                recorded_document, actual_document = _mask(
                    recorded_document,
                    actual_document,
                    list(iter_decoded_pointer_segments(pointer)),
                    expected=(value.recorded, value.fresh),
                )
        return recorded_document == actual_document
    return False


class _Masked:
    """Placeholder for a value both bodies hold but that is expected to differ between them."""


_MASKED = _Masked()


def _mask(
    recorded: object, actual: object, segments: list[str], expected: tuple[object, object] | None = None
) -> tuple[object, object]:
    """Replace the scalars at `segments` in both documents with a placeholder.

    Without `expected`, both must be of the same type; with it, they must be exactly the expected pair.
    """
    if not segments:
        if expected is None:
            matches = _is_scalar(recorded) and type(recorded) is type(actual)
        else:
            matches = _is_exactly(recorded, expected[0]) and _is_exactly(actual, expected[1])
        return (_MASKED, _MASKED) if matches else (recorded, actual)
    head, rest = segments[0], segments[1:]
    if isinstance(recorded, dict) and isinstance(actual, dict) and head in recorded and head in actual:
        left, right = _mask(recorded[head], actual[head], rest, expected)
        return {**recorded, head: left}, {**actual, head: right}
    if isinstance(recorded, list) and isinstance(actual, list) and head.isdigit():
        index = int(head)
        if index < len(recorded) and index < len(actual):
            left, right = _mask(recorded[index], actual[index], rest, expected)
            return [*recorded[:index], left, *recorded[index + 1 :]], [*actual[:index], right, *actual[index + 1 :]]
    return recorded, actual


def _is_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool))


def _is_exactly(value: object, expected: object) -> bool:
    # Types must match so `1` never stands in for `True`.
    return _is_scalar(value) and type(value) is type(expected) and value == expected
