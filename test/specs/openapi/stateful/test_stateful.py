import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis.errors import InvalidDefinition

import schemathesis
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.failures import FailureGroup
from schemathesis.generation.stateful.state_machine import (
    DEFAULT_STATE_MACHINE_SETTINGS,
    NO_LINKS_ERROR_MESSAGE,
    StepResult,
)
from schemathesis.specs.openapi.stateful import make_response_filter, match_status_code


@pytest.mark.parametrize(
    ("response_status", "filter_value", "matching"),
    [
        (200, 200, True),
        (200, 201, False),
        (200, "20X", True),
    ],
)
def test_match_status_code(response_status, filter_value, matching, response_factory):
    # When the response has `response_status` status
    response = response_factory.requests(status_code=response_status)
    # And the filter should filter by `filter_value`
    filter_function = match_status_code(filter_value)
    assert filter_function.__name__ == f"match_{filter_value}_response"
    # Then that response should match or not depending on the `matching` value
    assert filter_function(StepResult(response, None)) is matching


@pytest.mark.parametrize(
    ("response_status", "status_codes", "matching"),
    [
        (202, (200, "default"), True),
        (200, (200, "default"), False),
        (200, ("20X", "default"), False),
        (210, ("20X", "default"), True),
    ],
)
def test_default_status_code(response_status, status_codes, matching, response_factory):
    response = response_factory.requests(status_code=response_status)
    filter_function = make_response_filter("default", status_codes)
    assert filter_function(StepResult(response, None)) is matching


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
    suppress_health_check=list(HealthCheck),
    phases=[Phase.generate],
    stateful_step_count=3  # There is no need for longer sequences to uncover the bug
)
""",
        schema=app_schema,
    )
    result = testdir.runpytest()
    # Then it should be able to find a hidden error:
    result.assert_outcomes(failed=1)
    # And there should be cURL command to reproduce the error in the GET call
    result.stdout.re_match_lines([rf".+curl -X GET '{openapi3_base_url}/users/\w+.+"])


def removeprefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


@pytest.mark.parametrize("factory_name", ["wsgi_app_factory", "asgi_app_factory"])
def test_hidden_failure_app(request, factory_name, open_api_3):
    factory = request.getfixturevalue(factory_name)
    app = factory(operations=("create_user", "get_user", "update_user"), version=open_api_3)

    if factory_name == "asgi_app_factory":
        schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)
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
        schema = schemathesis.openapi.from_wsgi("/schema.yaml", app=app)

    state_machine = schema.as_state_machine()

    with pytest.raises(FailureGroup) as exc:
        state_machine.run(
            settings=settings(
                max_examples=2000,
                deadline=None,
                suppress_health_check=list(HealthCheck),
                phases=[Phase.generate],
                stateful_step_count=3,
            )
        )
    failures = [str(e) for e in exc.value.exceptions]
    assert (
        "Undocumented HTTP status code" in failures[0]
        or "Undocumented HTTP status code" in failures[1]
        or "Undocumented HTTP status code" in failures[2]
    )
    assert "Server error" in failures[0] or "Server error" in failures[1] or "Server error" in failures[2]


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
    suppress_health_check=list(HealthCheck),
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


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_no_transitions_error(app_schema):
    schema = schemathesis.openapi.from_dict(app_schema)
    state_machine_cls = schema.as_state_machine()

    with pytest.raises(IncorrectUsage, match=NO_LINKS_ERROR_MESSAGE):
        state_machine_cls()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_settings_error(app_schema):
    schema = schemathesis.openapi.from_dict(app_schema)

    class Workflow(schema.as_state_machine()):
        settings = settings(max_examples=5)

    with pytest.raises(InvalidDefinition):
        Workflow()


@pytest.mark.parametrize("merge_body", [True, False])
def test_dynamic_body(merge_body, app_factory):
    app = app_factory(merge_body=merge_body)
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)
    state_machine = schema.as_state_machine()

    state_machine.run(
        settings=settings(
            max_examples=100,
            deadline=None,
            suppress_health_check=list(HealthCheck),
            phases=[Phase.generate],
            stateful_step_count=2,
        )
    )


def test_custom_config_in_test_case(app_factory):
    app = app_factory()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)
    settings = schema.as_state_machine().TestCase.settings
    for key, value in DEFAULT_STATE_MACHINE_SETTINGS.__dict__.items():
        assert getattr(settings, key) == value
