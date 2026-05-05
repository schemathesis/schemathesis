from __future__ import annotations

import time
import unittest
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING
from warnings import catch_warnings

from hypothesis.errors import InvalidArgument
from jsonschema.exceptions import SchemaError as JsonSchemaError
from jsonschema_rs import ValidationError
from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext
from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import (
    SERIALIZERS_SUGGESTION_MESSAGE,
    AuthenticationError,
    InternalError,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    SchemaLocation,
    is_regex_validation_error,
)
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.errors import (
    DeadlineExceeded,
    TestingState,
    UnexpectedError,
    clear_hypothesis_notes,
    deduplicate_errors,
)
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import record_extra_data_from_recorder
from schemathesis.engine.run.unit._errors import (
    get_invalid_regular_expression_message,
    iter_mark_error_events,
)
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis.reporting import (
    build_health_check_error,
    build_unsatisfiable_error,
    ignore_hypothesis_output,
)

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def run_test(
    *,
    operation: APIOperation,
    test_function: Callable,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> events.EventGenerator:
    """A single test run with all error handling needed."""
    import hypothesis.errors

    errors: list[Exception] = []
    skip_reason = None
    error: Exception
    test_start_time = time.monotonic()
    recorder = ScenarioRecorder(label=operation.label)
    state = TestingState()

    def non_fatal_error(error: Exception, code_sample: str | None = None) -> events.NonFatalError:
        return events.NonFatalError(
            error=error, phase=phase, label=operation.label, related_to_operation=True, code_sample=code_sample
        )

    def scenario_finished(status: Status) -> events.ScenarioFinished:
        return events.ScenarioFinished(
            id=scenario_id,
            suite_id=suite_id,
            phase=phase,
            label=operation.label,
            recorder=recorder,
            status=status,
            elapsed_time=time.monotonic() - test_start_time,
            skip_reason=skip_reason,
            is_final=False,
        )

    phase_name = phase.value.lower()
    assert phase_name in ("examples", "coverage", "fuzzing", "stateful")

    operation_config = ctx.config.operations.get_for_operation(operation)
    continue_on_failure = operation_config.continue_on_failure or ctx.config.continue_on_failure or False
    generation = ctx.config.generation_for(operation=operation, phase=phase_name)
    override = overrides.for_operation(ctx.config, operation=operation)
    auth = ctx.config.auth_for(operation=operation)
    headers = ctx.config.headers_for(operation=operation)
    transport_kwargs = ctx.get_transport_kwargs(operation=operation)
    check_ctx = CheckContext(
        override=override,
        auth=auth,
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=ctx.config.checks_config_for(operation=operation, phase=phase_name),
        transport_kwargs=transport_kwargs,
        recorder=recorder,
    )

    if ctx.error_feedback is not None:
        ctx.error_feedback.checkpoint()

    try:
        setup_hypothesis_database_key(test_function, operation)
        with catch_warnings(record=True) as warnings, ignore_hypothesis_output():
            test_function(
                ctx=ctx,
                state=state,
                errors=errors,
                check_ctx=check_ctx,
                recorder=recorder,
                generation=generation,
                transport_kwargs=transport_kwargs,
                continue_on_failure=continue_on_failure,
            )
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        status = Status.SUCCESS
    except (SkipTest, unittest.case.SkipTest) as exc:
        status = Status.SKIP
        skip_reason = {"Hypothesis has been told to run no examples for this test.": "No examples in schema"}.get(
            str(exc), str(exc)
        )
    except (FailureGroup, Failure):
        status = Status.FAILURE
    except UnexpectedError:
        # It could be an error in user-defined extensions, network errors or internal Schemathesis errors
        status = Status.ERROR
    except hypothesis.errors.Flaky as exc:
        if isinstance(exc.__cause__, hypothesis.errors.DeadlineExceeded):
            status = Status.ERROR
            yield non_fatal_error(DeadlineExceeded.from_exc(exc.__cause__))
        elif isinstance(exc, hypothesis.errors.FlakyFailure) and any(
            isinstance(subexc, hypothesis.errors.DeadlineExceeded) for subexc in exc.exceptions
        ):
            for sub_exc in exc.exceptions:
                if isinstance(sub_exc, hypothesis.errors.DeadlineExceeded):
                    yield non_fatal_error(DeadlineExceeded.from_exc(sub_exc))
            status = Status.ERROR
        elif errors:
            status = Status.ERROR
        else:
            # Unrecoverable network errors (e.g. timeouts) are not appended to `errors`
            # and are re-raised so Hypothesis sees the original exception; surface them
            # here so a replay-induced `Flaky` is not misclassified as a check failure.
            unrecoverable = state.unrecoverable_network_error
            if unrecoverable is not None:
                status = Status.ERROR
                yield non_fatal_error(unrecoverable.error, code_sample=unrecoverable.code_sample)
            else:
                status = Status.FAILURE
    except BaseExceptionGroup as exc:
        status = Status.ERROR
        # Check for errors in the exception group
        for sub_exc in exc.exceptions:
            if is_regex_validation_error(sub_exc):
                yield non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(sub_exc))
            elif isinstance(sub_exc, InvalidSchema):
                yield non_fatal_error(sub_exc)
            else:
                code_sample = state.get_code_sample_for(sub_exc)
                if code_sample is not None:
                    clear_hypothesis_notes(sub_exc)
                    yield non_fatal_error(sub_exc, code_sample=code_sample)
    except hypothesis.errors.FailedHealthCheck as exc:
        status = Status.ERROR
        yield non_fatal_error(build_health_check_error(operation, exc, with_tip=False))
    except hypothesis.errors.Unsatisfiable:
        # We need more clear error message here
        status = Status.ERROR
        yield non_fatal_error(
            build_unsatisfiable_error(operation, with_tip=False, filter_tracker=operation.filter_case_tracker)
        )
    except AuthenticationError as exc:
        status = Status.ERROR
        yield non_fatal_error(exc)
    except KeyboardInterrupt:
        yield scenario_finished(Status.INTERRUPTED)
        yield events.Interrupted(phase=phase)
        return
    except AssertionError as exc:  # May come from `hypothesis-jsonschema` or `hypothesis`
        status = Status.ERROR
        try:
            operation.schema.validate()
            # JSON Schema validation can miss it if there is `$ref` adjacent to `type` on older specifications
            if str(exc).startswith("Unknown type"):
                yield non_fatal_error(
                    InvalidSchema(
                        message=str(exc),
                        path=operation.path,
                        method=operation.method,
                    )
                )
            else:
                msg = "Unexpected error during testing of this API operation"
                exc_msg = str(exc)
                if exc_msg:
                    msg += f": {exc_msg}"
                try:
                    raise InternalError(msg) from exc
                except InternalError as exc:
                    yield non_fatal_error(exc)
        except ValidationError as exc:
            yield non_fatal_error(
                InvalidSchema.from_jsonschema_error(
                    exc,
                    path=operation.path,
                    method=operation.method,
                    config=ctx.config.output,
                    location=SchemaLocation.maybe_from_error_path(exc.instance_path, ctx.schema.specification.version),
                )
            )
    except InvalidArgument as exc:
        status = Status.ERROR
        message = get_invalid_regular_expression_message(warnings)
        if message:
            # `hypothesis-jsonschema` emits a warning on invalid regular expression syntax
            yield non_fatal_error(InvalidRegexPattern.from_hypothesis_jsonschema_message(message))
        else:
            health_check = build_health_check_error(operation, exc, with_tip=False)
            if isinstance(health_check, hypothesis.errors.FailedHealthCheck):
                yield non_fatal_error(health_check)
            else:
                yield non_fatal_error(exc)
    except hypothesis.errors.DeadlineExceeded as exc:
        status = Status.ERROR
        yield non_fatal_error(DeadlineExceeded.from_exc(exc))
    except JsonSchemaError as exc:
        status = Status.ERROR
        yield non_fatal_error(InvalidRegexPattern.from_schema_error(exc, from_examples=False))
    except ValidationError as exc:
        status = Status.ERROR
        if is_regex_validation_error(exc):
            yield non_fatal_error(InvalidRegexPattern.from_jsonschema_rs_error(exc))
        else:
            code_sample = state.get_code_sample_for(exc)
            yield non_fatal_error(exc, code_sample=code_sample)
    except Exception as exc:
        status = Status.ERROR
        clear_hypothesis_notes(exc)
        # Likely a YAML parsing issue. E.g. `00:00:00.00` (without quotes) is parsed as float `0.0`
        if str(exc) == "first argument must be string or compiled pattern":
            yield non_fatal_error(
                InvalidRegexType(
                    "Invalid `pattern` value: expected a string. "
                    "If your schema is in YAML, ensure `pattern` values are quoted",
                )
            )
        else:
            code_sample = state.get_code_sample_for(exc)
            yield non_fatal_error(exc, code_sample=code_sample)
    if (
        status == Status.SUCCESS
        and continue_on_failure
        and any(check.status == Status.FAILURE for checks in recorder.checks.values() for check in checks)
    ):
        status = Status.FAILURE

    for event in iter_mark_error_events(
        test_function=test_function,
        non_fatal_error=non_fatal_error,
        current_status=status,
        serializers_suggestion=SERIALIZERS_SUGGESTION_MESSAGE,
    ):
        status = Status.ERROR
        yield event

    for error in deduplicate_errors(errors):
        yield non_fatal_error(error)

    record_extra_data_from_recorder(ctx, operation, recorder)

    yield scenario_finished(status)


def setup_hypothesis_database_key(test: Callable, operation: APIOperation) -> None:
    """Make Hypothesis use separate database entries for every API operation.

    It increases the effectiveness of the Hypothesis database in the CLI.
    """
    test.hypothesis.inner_test._hypothesis_internal_add_digest = operation.label.encode("utf8")  # type: ignore[attr-defined]
