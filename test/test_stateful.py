import re

import pytest

import schemathesis
from schemathesis.extra.pytest_plugin import NOT_USED_STATEFUL_TESTING_MESSAGE
from schemathesis.models import MISSING_STATEFUL_ARGUMENT_MESSAGE
from schemathesis.specs.openapi import expressions
from schemathesis.stateful import ParsedData
from schemathesis.utils import NOT_SET

from .apps.utils import OpenAPIVersion


@pytest.mark.parametrize(
    "parameters, body", (({"a": 1}, None), ({"a": 1}, NOT_SET), ({"a": 1}, {"value": 1}), ({"a": 1}, [1, 2, 3]))
)
def test_hashable(parameters, body):
    # All parsed data should be hashable
    hash(ParsedData(parameters, body))


@pytest.fixture
def openapi_version():
    return OpenAPIVersion("3.0")


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_enabled(testdir, app_schema, openapi3_base_url):
    # When "stateful" is used in the "parametrize" decorator
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 4 tests in total:
    # 1 - the original one for POST /users
    # 3 - stateful ones:
    #   - POST /users -> GET /users
    #   - POST /users -> GET /users -> PATCH /users
    #   - POST /users -> PATCH /users
    result = testdir.run_and_assert("-v", passed=4)
    result.stdout.re_match_lines(
        [
            r"test_stateful_enabled.py::test_\[POST:/api/users/\]\[P\] PASSED",
            r"test_stateful_enabled.py::test_\[POST:/api/users/ -> GET:/api/users/{user_id}\]"
            r"\[P\] PASSED * \[ 66%\]",
            r"test_stateful_enabled.py::test_\[POST:/api/users/ -> GET:/api/users/{user_id} -> "
            r"PATCH:/api/users/{user_id}\]\[P\] PASSED * \[ 75%\]",
            r"test_stateful_enabled.py::test_\[POST:/api/users/ -> PATCH:/api/users/{user_id}\]"
            r"\[P\] PASSED * \[100%\]",
            r"Hypothesis calls: 8",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_enabled_limit(testdir, app_schema, openapi3_base_url):
    # When "stateful" is used in the "parametrize" decorator
    # And "stateful_recursion_limit" is set to some number
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, stateful_recursion_limit=1, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 3 tests in total:
    # 1 - the original one for POST /users
    # 2 - stateful ones:
    #   - POST /users -> GET /users
    #   - POST /users -> PATCH /users
    result = testdir.run_and_assert("-v", passed=3)
    result.stdout.re_match_lines(
        [
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/\]\[P\] PASSED",
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/ -> GET:/api/users/{user_id}\]"
            r"\[P\] PASSED * \[ 66%\]",
            r"test_stateful_enabled_limit.py::test_\[POST:/api/users/ -> PATCH:/api/users/{user_id}\]"
            r"\[P\] PASSED * \[100%\]",
            r"Hypothesis calls: 6",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_disabled(testdir, app_schema, openapi3_base_url):
    # When "stateful" is NOT used in the "parametrize" decorator
    testdir.make_test(
        f"""
@schema.parametrize(method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    case.validate_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be 1 test in total - the original one for POST /users
    result = testdir.run_and_assert("-v", passed=1)
    result.stdout.re_match_lines(
        [
            r"test_stateful_disabled.py::test_\[POST:/api/users/\]\[P\] PASSED",
            r"Hypothesis calls: 2",
        ]
    )


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_stateful_not_used(testdir, app_schema):
    # When "stateful" is used in the "parametrize" decorator
    # And the test doesn't use "Case.call" or "Case.store_response" to store the feedback
    testdir.make_test(
        """
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
        """,
        schema=app_schema,
    )
    # Then there should be 1 test in total - the original one for POST /users
    result = testdir.run_and_assert("-v", passed=1)
    result.stdout.re_match_lines(
        [
            r"test_stateful_not_used.py::test_\[POST:/api/users/\]\[P\] PASSED",
            r"Hypothesis calls: 2",
        ]
    )
    # And a warning should be risen because responses were not stored
    assert f"  test_stateful_not_used.py:10: PytestWarning: {NOT_USED_STATEFUL_TESTING_MESSAGE}" in result.stdout.lines


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_store_response_without_stateful(testdir, app_schema, openapi3_base_url):
    # When "stateful" is NOT used in the "parametrize" decorator
    # And "case.store_response" is used inside the test
    testdir.make_test(
        f"""
@schema.parametrize(method="POST")
@settings(max_examples=2)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    response = case.call(base_url="{openapi3_base_url}")
    with pytest.raises(RuntimeError, match="{MISSING_STATEFUL_ARGUMENT_MESSAGE}"):
        case.store_response(response)
        """,
        schema=app_schema,
    )
    # Then there should be an exception, that is verified via the pytest.raises ctx manager
    testdir.run_and_assert(passed=1)


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_no_warning_on_failure(testdir, app_schema):
    # When a stateful test fails
    testdir.make_test(
        f"""
@schema.parametrize(stateful=Stateful.links, method="POST")
@settings(max_examples=2)
def test_(case):
    1 / 0
        """,
        schema=app_schema,
    )
    # Then there should be no warning about not using the "stateful" argument
    result = testdir.run_and_assert(failed=1)
    warning_text = f"  test_no_warning_on_failure.py:10: PytestWarning: {NOT_USED_STATEFUL_TESTING_MESSAGE}"
    assert warning_text not in result.stdout.lines


def add_link(schema, target, **kwargs):
    schema.add_link(source=schema["/users/"]["POST"], target=target, status_code="201", **kwargs)
    return schema["/users/"]["POST"].definition.resolved["responses"]["201"]["links"]


EXPECTED_LINK_PARAMETERS = {"parameters": {"userId": "$response.body#/id"}}


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_default(schema_url):
    schema = schemathesis.from_uri(schema_url)
    # When we add a link to the target endpoint
    # And it is an `Endpoint` instance
    # And it has the `operationId` key
    links = add_link(schema, schema["/users/{user_id}"]["GET"], parameters={"userId": "$response.body#/id"})
    # Then it should be added without errors
    assert links[schema["/users/{user_id}"]["GET"].verbose_name] == {
        "operationId": "getUser",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.parametrize("status_code", ("201", 201))
@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_no_endpoints_cache(schema_url, status_code):
    schema = schemathesis.from_uri(schema_url)
    # When we add a link to the target endpoint
    source = schema["/users/"]["POST"]
    target = schema["/users/{user_id}"]["GET"]
    # And the endpoints are not cached
    delattr(schema, "_endpoints")
    schema.add_link(
        source=source,
        target=target,
        status_code=status_code,
        parameters={"userId": "$response.body#/id"},
    )
    # Then it should be added without errors
    # And the cache cleanup should be no-op
    links = schema["/users/"]["POST"].definition.resolved["responses"]["201"]["links"]
    assert links[schema["/users/{user_id}"]["GET"].verbose_name] == {
        "operationId": "getUser",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_no_operation_id(schema_url):
    schema = schemathesis.from_uri(schema_url)
    target = schema["/users/{user_id}"]["GET"]
    del target.definition.resolved["operationId"]
    links = add_link(schema, schema["/users/{user_id}"]["GET"], parameters={"userId": "$response.body#/id"})
    assert links[schema["/users/{user_id}"]["GET"].verbose_name] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_by_reference(schema_url):
    schema = schemathesis.from_uri(schema_url)
    links = add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"userId": "$response.body#/id"})
    assert links["#/paths/~1users~1{user_id}/get"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_by_reference_twice(schema_url):
    schema = schemathesis.from_uri(schema_url)
    add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"userId": "$response.body#/id"})
    links = add_link(schema, "#/paths/~1users~1{user_id}/get", request_body="#/foo")
    assert links["#/paths/~1users~1{user_id}/get"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        **EXPECTED_LINK_PARAMETERS,
    }
    assert links["#/paths/~1users~1{user_id}/get_new"] == {
        "operationRef": "#/paths/~1users~1{user_id}/get",
        "requestBody": "#/foo",
    }


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_behind_a_reference(schema_url):
    # See GH-824
    schema = schemathesis.from_uri(schema_url)
    # When all methods for an endpoint are behind a reference
    schema.raw_schema["components"]["methods"] = {
        "users": schema.raw_schema["paths"]["/users/"],
        "user-details": schema.raw_schema["paths"]["/users/{user_id}"],
    }
    schema.raw_schema["paths"]["/users/"] = {"$ref": "#/components/methods/users"}
    schema.raw_schema["paths"]["/users/{user_id}"] = {"$ref": "#/components/methods/user-details"}
    assert not hasattr(schema, "_endpoints")
    # And a link is added
    add_link(schema, schema["/users/{user_id}"]["GET"], parameters={"userId": "$response.body#/id"})
    # Then the source endpoint should have the new link
    endpoint = schema["/users/"]["POST"]
    links = endpoint.definition.resolved["responses"]["201"]["links"]
    assert len(links) == 3
    assert links["GET /users/{user_id}"] == {"parameters": {"userId": "$response.body#/id"}, "operationId": "getUser"}


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_nothing_is_provided(schema_url):
    schema = schemathesis.from_uri(schema_url)
    # When the user doesn't provide parameters or request_body
    with pytest.raises(ValueError, match="You need to provide `parameters` or `request_body`."):
        # Then there should be an error
        schema.add_link(
            source=schema["/users/"]["POST"],
            target="#/paths/~1users~1{user_id}/get",
            status_code="201",
        )


@pytest.mark.parametrize(
    "change, message",
    (
        (lambda s, e: setattr(e, "method", "GET"), "No such endpoint: `GET /users/`. Did you mean `POST /users/`?"),
        (lambda s, e: setattr(e, "path", "/userz/"), "No such endpoint: `POST /userz/`. Did you mean `POST /users/`?"),
        (
            lambda s, e: s.raw_schema["paths"].__setitem__("/users/", {}),
            "No such endpoint: `POST /users/`.",
        ),
    ),
)
@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_add_link_unknown_endpoint(schema_url, change, message):
    schema = schemathesis.from_uri(schema_url)
    # When the source endpoint is modified and can't be found
    source = schema["/users/"]["POST"]
    change(schema, source)
    with pytest.raises(
        ValueError, match=re.escape(f"{message} Check if the requested endpoint passes the filters in the schema.")
    ):
        # Then there should be an error about it.
        schema.add_link(source=source, target="#/paths/~1users~1{user_id}/get", status_code="201", request_body="#/foo")


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_links_access(schema_url):
    schema = schemathesis.from_uri(schema_url)
    links = schema["/users/"]["POST"].links["201"]
    assert len(links) == 2
    assert links["GetUserByUserId"].name == "GetUserByUserId"


@pytest.mark.endpoints("create_user", "get_user", "update_user")
def test_misspelled_parameter(schema_url):
    schema = schemathesis.from_uri(schema_url)
    # When the user supplies a parameter definition, that points to location which has no parameters defined in the
    # schema
    add_link(schema, "#/paths/~1users~1{user_id}/get", parameters={"header.userId": "$response.body#/id"})
    case = schema["/users/{user_id}"]["GET"].make_case()
    link = schema["/users/"]["POST"].links["201"]["#/paths/~1users~1{user_id}/get"]
    with pytest.raises(
        ValueError, match="No such parameter in `GET /users/{user_id}`: `userId`. Did you mean `user_id`?"
    ):
        link.set_data(case, context=expressions.ExpressionContext(case=case, response=None))
