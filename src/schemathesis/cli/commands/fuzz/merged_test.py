from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import hypothesis
import requests
from hypothesis import given, settings
from hypothesis import strategies as st
from requests.exceptions import ChunkedEncodingError
from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext, run_checks
from schemathesis.cli.commands.fuzz.scheduler import OperationStrategy, build_weighted_operation_table
from schemathesis.cli.commands.fuzz.unguided import _FuzzingStopped
from schemathesis.cli.commands.run.context import GroupedFailures
from schemathesis.config import ProjectConfig
from schemathesis.core.errors import InvalidSchema, SerializationNotPossible
from schemathesis.core.failures import Failure
from schemathesis.core.result import Err
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import metrics, overrides
from schemathesis.resources import ExtraDataSource

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, BaseSchema


class NoValidFuzzOperationsError(Exception):
    """Raised when no valid operations are available for fuzzing."""

    __slots__ = ("errors",)

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        if errors:
            details = "\n".join(str(error) for error in errors)
            message = f"No valid operations available for fuzzing:\n{details}"
        else:
            message = "No valid operations available for fuzzing."
        super().__init__(message)


class _ThreadLocalState(threading.local):
    session: requests.Session | None

    __slots__ = ("session",)

    def __init__(self) -> None:
        self.session = None


def _seed_for_worker(seed: int, worker_id: int) -> int:
    return seed + worker_id


def build_check_context(case: Case, config: ProjectConfig, transport_kwargs: dict[str, Any]) -> CheckContext:
    headers = config.headers_for(operation=case.operation)
    return CheckContext(
        override=overrides.for_operation(config, operation=case.operation),
        auth=config.auth_for(operation=case.operation),
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=config.checks_config_for(operation=case.operation, phase="fuzzing"),
        transport_kwargs=transport_kwargs,
    )


def build_merged_test(
    schema: BaseSchema,
    *,
    config: ProjectConfig,
    extra_data_source: ExtraDataSource | None = None,
    on_grouped_failure: Callable[[str, GroupedFailures], None] | None = None,
    on_scenario_started: Callable[[str], uuid.UUID] | None = None,
    on_scenario_finished: Callable[[uuid.UUID, str, ScenarioRecorder, Status, float], None] | None = None,
    on_non_fatal_error: Callable[[str, Exception], None] | None = None,
    on_invalid_operation: Callable[[str, Exception], None] | None = None,
    continue_on_failure: bool = False,
    stop_event: threading.Event | None = None,
    worker_id: int = 0,
) -> Callable:
    strategy_kwargs: dict[str, Any] = {}
    if extra_data_source is not None:
        strategy_kwargs["extra_data_source"] = extra_data_source
    thread_local = _ThreadLocalState()
    transport_kwargs_cache: dict[str, dict[str, Any]] = {}
    transport_kwargs_cache_lock = threading.Lock()
    seen_inputs: set[int] = set()
    seen_failures: dict[int, BaseException] = {}
    seen_lock = threading.Lock()

    def get_session() -> requests.Session:
        session = thread_local.session
        if session is not None:
            return session
        session = requests.Session()
        session.headers = {}
        thread_local.session = session
        return session

    def get_transport_kwargs(operation: APIOperation) -> dict[str, Any]:
        key = operation.label
        cached = transport_kwargs_cache.get(key)
        if cached is None:
            built: dict[str, Any] = {
                "headers": config.headers_for(operation=operation),
                "max_redirects": config.max_redirects_for(operation=operation),
                "timeout": config.request_timeout_for(operation=operation),
                "verify": config.tls_verify_for(operation=operation),
                "cert": config.request_cert_for(operation=operation),
                "auth": config.auth_for(operation=operation),
            }
            proxy = config.proxy_for(operation=operation)
            if proxy is not None:
                built["proxies"] = {"all": proxy}
            with transport_kwargs_cache_lock:
                current = transport_kwargs_cache.get(key)
                if current is None:
                    transport_kwargs_cache[key] = built
                    cached = built
                else:
                    cached = current
        kwargs = cached.copy()
        kwargs["session"] = get_session()
        return kwargs

    operations: list[OperationStrategy] = []
    invalid_errors: list[Exception] = []
    for result in schema.get_all_operations():
        if isinstance(result, Err):
            schema_error = result.err()
            label = "Schema"
            if (
                isinstance(schema_error, InvalidSchema)
                and schema_error.method is not None
                and schema_error.path is not None
            ):
                label = f"{schema_error.method.upper()} {schema_error.path}"
            invalid_errors.append(schema_error)
            if on_invalid_operation is not None:
                on_invalid_operation(label, schema_error)
            continue
        operation = result.ok()
        content_types = operation.get_request_payload_content_types()
        if content_types and all(
            operation.schema.transport.get_first_matching_media_type(media_type) is None for media_type in content_types
        ):
            serialization_error = SerializationNotPossible.from_media_types(*sorted(content_types))
            invalid_errors.append(serialization_error)
            if on_invalid_operation is not None:
                on_invalid_operation(operation.label, serialization_error)
            continue
        generation = config.generation_for(operation=operation, phase="fuzzing")
        try:
            mode_strategies = [
                operation.as_strategy(generation_mode=generation_mode, **strategy_kwargs)
                for generation_mode in generation.modes
            ]
        except Exception as strategy_error:
            invalid_errors.append(strategy_error)
            if on_invalid_operation is not None:
                on_invalid_operation(operation.label, strategy_error)
            continue
        operations.append(OperationStrategy(operation=operation, strategy=st.one_of(mode_strategies)))
    if not operations:
        raise NoValidFuzzOperationsError(invalid_errors)

    weighted_operation_table = build_weighted_operation_table(operations, seed=config.seed, worker_id=worker_id)
    merged_strategy = st.sampled_from(weighted_operation_table).flatmap(lambda item: item.strategy)

    def execute_case(case: Case) -> None:
        scenario_id = on_scenario_started(case.operation.label) if on_scenario_started is not None else uuid.uuid4()
        start_time = time.monotonic()
        recorder = ScenarioRecorder(label=case.operation.label)
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        status = Status.SUCCESS
        generation = config.generation_for(operation=case.operation, phase="fuzzing")

        try:
            transport_kwargs = get_transport_kwargs(case.operation)
            check_ctx = build_check_context(case, config, transport_kwargs)
            response = case.call(**transport_kwargs)
            recorder.record_response(case_id=case.id, response=response)
            metrics.maximize(generation.maximize, case=case, response=response)
            if extra_data_source is not None:
                if extra_data_source.should_record(operation=case.operation.label):
                    extra_data_source.record_response(operation=case.operation, response=response, case=case)
                # Record DELETE attempts immediately to influence subsequent strategy draws.
                # Include both successful (2xx) and 404 responses to avoid repeatedly
                # hitting already-deleted resources.
                status_code = response.status_code
                if 200 <= status_code < 300 or status_code == 404:
                    extra_data_source.record_successful_delete(operation=case.operation, case=case)

            try:
                code_sample = case.as_curl_command(headers=dict(response.request.headers), verify=response.verify)
            except Exception:
                code_sample = ""

            def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
                collected.add(failure)
                recorder.record_check_failure(name=name, case_id=case.id, code_sample=code_sample, failure=failure)

            def on_success(name: str, _case: Case) -> None:
                recorder.record_check_success(name=name, case_id=_case.id)

            collected = run_checks(
                case=case,
                response=response,
                ctx=check_ctx,
                checks=check_ctx._checks,
                on_failure=on_failure,
                on_success=on_success,
            )
            if collected:
                status = Status.FAILURE
                if on_grouped_failure is not None:
                    try:
                        norm_response = response if isinstance(response, Response) else Response.from_any(response)
                    except Exception:
                        norm_response = None
                    on_grouped_failure(
                        case.operation.label,
                        GroupedFailures(
                            case_id=case.id,
                            code_sample=code_sample,
                            failures=sorted(collected),
                            response=norm_response,
                        ),
                    )
            # Never raise on check failures — Hypothesis keeps generating examples
            # indefinitely instead of stopping and restarting on each failure.
        except (requests.Timeout, requests.ConnectionError, ChunkedEncodingError) as error:
            status = Status.ERROR
            if isinstance(error.request, requests.Request):
                recorder.record_request(case_id=case.id, request=error.request.prepare())
            elif isinstance(error.request, requests.PreparedRequest):
                recorder.record_request(case_id=case.id, request=error.request)
            if continue_on_failure:
                if on_non_fatal_error is not None:
                    on_non_fatal_error(case.operation.label, error)
                return
            raise
        except Exception as error:
            status = Status.ERROR
            if continue_on_failure:
                if on_non_fatal_error is not None:
                    on_non_fatal_error(case.operation.label, error)
                return
            raise
        except BaseException:
            status = Status.ERROR
            raise
        finally:
            if on_scenario_finished is not None:
                on_scenario_finished(scenario_id, case.operation.label, recorder, status, time.monotonic() - start_time)

    def test(case: Case) -> None:
        # Allow the fuzz loop to be stopped from outside without restarting Hypothesis.
        if stop_event is not None and stop_event.is_set():
            raise _FuzzingStopped

        generation = config.generation_for(operation=case.operation, phase="fuzzing")
        if not generation.unique_inputs:
            execute_case(case)
            return

        cache_key = hash(case)
        with seen_lock:
            if cache_key in seen_failures:
                raise seen_failures[cache_key]
            if cache_key in seen_inputs:
                return

        try:
            execute_case(case)
        except BaseException as exc:
            with seen_lock:
                seen_failures[cache_key] = exc
            raise
        else:
            with seen_lock:
                seen_inputs.add(cache_key)

    test_with_strategy = given(case=merged_strategy)(test)
    base_settings = config.get_hypothesis_settings(phase="fuzzing")
    test_with_settings = settings(base_settings, max_examples=sys.maxsize)(test_with_strategy)
    if config.seed is not None:
        test_with_settings = hypothesis.seed(_seed_for_worker(config.seed, worker_id))(test_with_settings)

    return test_with_settings
