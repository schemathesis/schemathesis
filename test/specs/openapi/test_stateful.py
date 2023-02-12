import pytest
from hypothesis import HealthCheck, settings

import schemathesis
from schemathesis.exceptions import CheckFailed
from schemathesis.specs.openapi.stateful.links import make_response_filter, match_status_code
from schemathesis.stateful import StepResult
from src.schemathesis.models import CaseSource, Check, Status
from src.schemathesis.runner.serialization import SerializedCheck


@pytest.mark.parametrize(
    "response_status, filter_value, matching",
    (
        (200, 200, True),
        (200, 201, False),
        (200, "20X", True),
    ),
)
def test_match_status_code(response_status, filter_value, matching, response_factory):
    # When the response has `response_status` status
    response = response_factory.requests(status_code=response_status)
    # And the filter should filter by `filter_value`
    filter_function = match_status_code(filter_value)
    assert filter_function.__name__ == f"match_{filter_value}_response"
    # Then that response should match or not depending on the `matching` value
    assert filter_function(StepResult(response, None, 1.0)) is matching


@pytest.mark.parametrize(
    "response_status, status_codes, matching",
    (
        (202, [200, "default"], True),
        (200, [200, "default"], False),
        (200, ["20X", "default"], False),
        (210, ["20X", "default"], True),
    ),
)
def test_default_status_code(response_status, status_codes, matching, response_factory):
    response = response_factory.requests(status_code=response_status)
    filter_function = make_response_filter("default", status_codes)
    assert filter_function(StepResult(response, None, 1.0)) is matching


def test_custom_rule(testdir, openapi3_base_url):
    # When the state machine contains a failing rule that does not expect `Case`
    testdir.make_test(
        f"""
from hypothesis.stateful import initialize, rule

schema.base_url = "{openapi3_base_url}"

class APIWorkflow(schema.as_state_machine()):

    def validate_response(self, response, case):
        pass

    @rule(data=st.just("foo"))
    def some(self, data):
        assert 0

TestStateful = APIWorkflow.TestCase
""",
    )
    result = testdir.runpytest()
    # Then the reproducing steps should be correctly displayed
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([r".*state.some\(data='foo'\)"])


# With the following tests we try to uncover a bug that requires multiple steps with a shared step
# There are three operations:
#   1. Create user via first & last name
#   2. Get user's details - returns id & full name
#   3. Update user - update first & last name
# The problem is that (3) allows you to update a user with non string last name, which is not detectable in its output.
# However, (2) will fail when it will try to create a full name. Reproducing this bug requires 3 steps:
#   1. Create a user
#   2. Update the user with an invalid last name
#   3. Get info about this user


def find_reproduction_code(lines):
    for prefix in ("", "E   ", "E       "):
        try:
            first = lines.index(f"{prefix}Falsifying example:") + 1
            last = lines.index(f"{prefix}state.teardown()") + 1
            break
        except ValueError:
            continue
    else:
        raise ValueError("Failed to get reproduction code")
    return "\n".join([removeprefix(line, "E   ") for line in lines[first:last]])


@pytest.mark.operations("create_user", "get_user", "update_user")
def test_hidden_failure(testdir, app_schema, openapi3_base_url):
    # When we run test as a state machine
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"
TestStateful = schema.as_state_machine().TestCase
TestStateful.settings = settings(
    max_examples=2000,
    deadline=None,
    suppress_health_check=HealthCheck.all(),
    stateful_step_count=3  # There is no need for longer sequences to uncover the bug
)
""",
        schema=app_schema,
    )
    result = testdir.runpytest()
    # Then it should be able to find a hidden error:
    result.assert_outcomes(failed=1)
    # And there should be Python code to reproduce the error in the GET call
    result.stdout.re_match_lines([rf"E +curl -X GET .+ '{openapi3_base_url}/users/\w+.+"])
    # And the reproducing example should work
    example = find_reproduction_code(result.outlines)
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"
APIWorkflow = schema.as_state_machine()
{example}
    """,
        schema=app_schema,
    )
    result = testdir.runpytest()
    assert "E   1. Received a response with 5xx status code: 500" in result.outlines


def removeprefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


@pytest.mark.parametrize("factory_name", ("wsgi_app_factory", "asgi_app_factory"))
def test_hidden_failure_app(request, factory_name, open_api_3):
    factory = request.getfixturevalue(factory_name)
    app = factory(operations=("create_user", "get_user", "update_user"), version=open_api_3)

    if factory_name == "asgi_app_factory":
        schema = schemathesis.from_asgi("/openapi.json", app=app)
        schema.add_link(
            source=schema["/users/"]["POST"],
            target=schema["/users/{user_id}"]["GET"],
            status_code="201",
            parameters={
                "path.user_id": "$response.body#/id",
                "query.uid": "$response.body#/id",
            },
        )
        schema.add_link(
            source=schema["/users/"]["POST"],
            target=schema["/users/{user_id}"]["PATCH"],
            status_code="201",
            parameters={"user_id": "$response.body#/id"},
        )
        schema.add_link(
            source=schema["/users/{user_id}"]["GET"],
            target="#/paths/~1users~1{user_id}/patch",
            status_code="200",
            parameters={"user_id": "$response.body#/id"},
            request_body={"first_name": "foo", "last_name": "bar"},
        )
    else:
        schema = schemathesis.from_wsgi("/schema.yaml", app=app)

    state_machine = schema.as_state_machine()

    with pytest.raises(CheckFailed, match="Received a response with 5xx status code: 500"):
        state_machine.run(
            settings=settings(
                max_examples=2000,
                deadline=None,
                suppress_health_check=HealthCheck.all(),
                stateful_step_count=3,
            )
        )


@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.openapi_version("3.0")
def test_explicit_headers_reproduction(testdir, openapi3_base_url, app_schema):
    # See GH-828
    # When the user specifies headers manually in the state machine
    testdir.make_test(
        f"""
schema.base_url = "{openapi3_base_url}"

class APIWorkflow(schema.as_state_machine()):
    def get_call_kwargs(self, case):
        return {{"headers": {{"X-Token": "FOOBAR"}}}}

    def validate_response(self, response, case):
        assert 0, "Explicit failure"

TestCase = APIWorkflow.TestCase
    """,
        schema=app_schema,
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    # Then these headers should be displayed in the generated Python code
    example = find_reproduction_code(result.outlines)
    assert "headers={'X-Token': 'FOOBAR'}" in example.splitlines()[1]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_step_override(testdir, app_schema, base_url):
    # See GH-970
    # When the user overrides the `step` method
    testdir.make_test(
        f"""
schema.base_url = "{base_url}"

class APIWorkflow(schema.as_state_machine()):

    def step(self, case, previous=None):
        raise ValueError("ERROR FOUND!")

TestStateful = APIWorkflow.TestCase
TestStateful.settings = settings(
    max_examples=1,
    deadline=None,
    derandomize=True,
    suppress_health_check=HealthCheck.all(),
)
""",
        schema=app_schema,
    )
    result = testdir.runpytest()
    # Then it should be overridden
    result.assert_outcomes(failed=1)
    # And the placed error should pop up to indicate that the overridden code is called
    result.stdout.re_match_lines([r".+ValueError: ERROR FOUND!"])


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("multiple_failures")
def test_trimmed_output(testdir, app_schema, base_url):
    # When an issue is found
    testdir.make_test(
        f"""
schema.base_url = "{base_url}"

TestStateful = schema.as_state_machine().TestCase
""",
        schema=app_schema,
    )
    result = testdir.runpytest("--tb=short")
    result.assert_outcomes(failed=1)
    # Then internal frames should not appear after the "Falsifying example" block
    assert " in step" not in result.stdout.str()


@pytest.mark.parametrize("method", ("requests", "werkzeug"))
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_history(testdir, app_schema, base_url, response_factory, method):
    # When cases are serialized
    schema = schemathesis.from_dict(app_schema)
    first = schema["/users/"]["POST"].make_case(body={"first_name": "Foo", "last_name": "bar"})
    factory = getattr(response_factory, method)
    first_response = factory(status_code=201)
    second = schema["/users/{user_id}"]["PATCH"].make_case(
        path_parameters={"user_id": 42}, body={"first_name": "SPAM", "last_name": "bar"}
    )
    second_response = factory(status_code=200)
    second.source = CaseSource(case=first, response=first_response, elapsed=10)
    third = schema["/users/{user_id}"]["GET"].make_case(path_parameters={"user_id": 42})
    third_response = factory(status_code=200)
    third.source = CaseSource(case=second, response=second_response, elapsed=10)
    check = Check(name="not_a_server_error", value=Status.success, response=third_response, elapsed=10, example=third)
    serialized = SerializedCheck.from_check(check)
    # Then they should store all history
    assert serialized.history[0].case.verbose_name == "PATCH /api/users/{user_id}"
    assert serialized.history[1].case.verbose_name == "POST /api/users/"
