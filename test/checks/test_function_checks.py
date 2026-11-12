import pytest

import schemathesis.checks
from schemathesis.checks import max_response_time, not_a_server_error, run_checks
from schemathesis.config import ChecksConfig
from schemathesis.core.failures import CustomFailure, FailureGroup, ResponseTimeExceeded, ServerError
from schemathesis.engine.recorder import ScenarioRecorder
from test.utils import check_context, make_case


@pytest.mark.parametrize(
    ("limit", "elapsed", "expected"),
    [
        (None, 10.0, None),
        (None, 10.5, ("GET /path", "Actual: 10500.00ms\nLimit: 10000.00ms", 10.5, 10.0)),
        (0.25, 0.25, None),
        (0.25, 0.3, ("GET /path", "Actual: 300.00ms\nLimit: 250.00ms", 0.3, 0.25)),
    ],
    ids=["default-limit-reached", "default-limit-exceeded", "limit-reached", "limit-exceeded"],
)
def test_max_response_time(ctx, swagger_20, response_factory, limit, elapsed, expected):
    config = ChecksConfig.from_dict({} if limit is None else {"max_response_time": limit})
    response = response_factory.core(elapsed=elapsed)
    case = make_case(ctx, swagger_20, {})
    if expected is None:
        assert max_response_time(check_context(config), response, case) is None
    else:
        with pytest.raises(ResponseTimeExceeded) as exc_info:
            max_response_time(check_context(config), response, case)
        failure = exc_info.value
        assert (failure.operation, failure.message, failure.elapsed, failure.deadline) == expected


def test_run_checks_reports_kept_failures_and_custom_assertions(ctx, response_factory):
    schema = ctx.openapi.load_schema({"/foo": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @schema.hooks.hook
    def filter_failure(context, failure, case, response):
        return not (isinstance(failure, ServerError) and failure.status_code == 502)

    def server_errors(ctx, response, case):
        raise FailureGroup(
            [ServerError(operation="GET /foo", status_code=502), ServerError(operation="GET /foo", status_code=503)]
        )

    def dropped(ctx, response, case):
        raise ServerError(operation="GET /foo", status_code=502)

    def custom(ctx, response, case):
        raise AssertionError("broken")

    def passing(ctx, response, case):
        return None

    case = schema["/foo"]["GET"].Case()
    recorder = ScenarioRecorder(label="run-checks")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    failures = []
    successes = []
    run_checks(
        case=case,
        response=response_factory.core(),
        ctx=check_context(recorder=recorder),
        checks=[server_errors, dropped, custom, passing],
        on_failure=lambda name, collected, failure: failures.append((name, failure)),
        on_success=lambda name, case: successes.append((name, case.id)),
    )

    (server_errors_name, server_error), (custom_name, custom) = failures
    assert (
        (server_errors_name, type(server_error), server_error.operation, server_error.status_code),
        (custom_name, type(custom), custom.operation, custom.title, custom.message),
    ) == (
        ("server_errors", ServerError, "GET /foo", 503),
        ("custom", CustomFailure, "GET /foo", "Custom check failed: `custom`", "broken"),
    )
    assert successes == [("dropped", case.id), ("passing", case.id)]
    assert recorder.filtered_failures == {case.id: 2}


@pytest.mark.parametrize("value", [500, 502])
def test_not_a_server_error(ctx, value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(ctx, swagger_20, {})
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(check_context(), response, case)
    assert exc_info.type.__name__ == "ServerError"


@pytest.mark.parametrize("status_code", [200, 500], ids=["success", "server-error"])
def test_public_check_context_without_class_checks(ctx, swagger_20, response_factory, status_code):
    context = schemathesis.CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None
    )
    response = response_factory.core(status_code=status_code)
    case = make_case(ctx, swagger_20, {})
    if status_code == 200:
        assert not_a_server_error(context, response, case) is None
    else:
        with pytest.raises(ServerError) as exc_info:
            not_a_server_error(context, response, case)
        assert (exc_info.value.operation, exc_info.value.status_code) == ("GET /path", 500)


def test_module_access():
    # It is done via `__getattr__`
    _ = schemathesis.checks.negative_data_rejection
    assert "negative_data_rejection" in dir(schemathesis.checks)
