import json
from unittest import SkipTest
from unittest.mock import ANY, Mock

import pytest
import requests
import strawberry
from hypothesis import HealthCheck, Phase, find, given, settings

import schemathesis
from schemathesis.checks import CheckContext, not_a_server_error
from schemathesis.config import ChecksConfig
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import LoaderError
from schemathesis.core.failures import AcceptedNegativeData, Failure, FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import USER_AGENT, Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.graphql.checks import GraphQLClientError, GraphQLServerError
from schemathesis.graphql.loaders import extract_schema_from_response, get_introspection_query
from schemathesis.specs.graphql.validation import is_client_error, validate_graphql_response
from schemathesis.specs.openapi.checks import (
    ensure_resource_availability,
    ignored_auth,
    negative_data_rejection,
    positive_data_acceptance,
    use_after_free,
)
from schemathesis.transport.prepare import get_default_headers
from schemathesis.transport.wsgi import WSGI_TRANSPORT
from test.utils import assert_requests_call


def test_raw_schema(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    assert schema.specification.name == "GraphQL"


def test_tags(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    assert schema["Query"]["getBooks"].tags is None


@pytest.mark.hypothesis_nested
def test_operation_strategy(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    strategy = schema["Query"]["getBooks"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def test(case):
        response = case.call()
        assert response.status_code < 500

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_wsgi_kwargs(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    strategy = schema["Query"]["getBooks"].as_strategy()
    case = strategy.example()
    expected = {
        "method": "POST",
        "path": "/graphql",
        "query_string": {},
        "json": {"query": case.body},
        "headers": {
            **get_default_headers(),
            "User-Agent": USER_AGENT,
            SCHEMATHESIS_TEST_CASE_HEADER: ANY,
            "Content-Type": "application/json",
        },
    }
    assert WSGI_TRANSPORT.serialize_case(case) == expected


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_base_url(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    schema.config.update(base_url="http://0.0.0.0:1234/something")

    # Then the base path is changed, in this case it is the only available path
    assert schema.base_path == "/something"
    strategy = schema["Query"]["getBooks"].as_strategy()
    case = strategy.example()
    # And all requests should go to the specified URL
    assert case.as_transport_kwargs()["url"] == "http://0.0.0.0:1234/something"


@pytest.mark.parametrize("kwargs", [{"body": "SomeQuery"}, {"body": b'{"query": "SomeQuery"}'}])
def test_make_case(ctx, kwargs):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = schema["Query"]["getBooks"].Case(**kwargs)
    assert_requests_call(case)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"content": b"INTERNAL SERVER ERROR", "content_type": "text/plain"}, "JSON deserialization error"),
        ({"content": b"[]"}, "Unexpected GraphQL Response"),
    ],
)
def test_response_validation(ctx, response_factory, kwargs, expected):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    response = response_factory.requests(status_code=200, **kwargs)
    case = schema["Query"]["getBooks"].Case(body="Q")
    with pytest.raises(Failure, match=expected):
        not_a_server_error(
            CheckContext(
                override=None,
                auth=None,
                headers=None,
                config=ChecksConfig(),
                transport_kwargs=None,
            ),
            response,
            case,
        )


def test_client_error(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = schema["Query"]["getBooks"].Case(body="invalid query")
    with pytest.raises(FailureGroup) as exc:
        case.call_and_validate()
    assert "Syntax Error: Unexpected Name 'invalid'." in str(exc.value.exceptions[0])


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Client error: no data, no path in error
        ({"data": None, "errors": [{"message": "Missing required arg"}]}, True),
        # Client error: explicit null data, no path
        ({"errors": [{"message": "Syntax error"}]}, True),
        # Server error: has path (resolver execution failed)
        ({"data": None, "errors": [{"message": "Resolver error", "path": ["field"]}]}, False),
        # Server error: has partial data
        ({"data": {"field": "value"}, "errors": [{"message": "Error"}]}, False),
        # No errors at all
        ({"data": {"field": "value"}}, False),
        # Empty errors array
        ({"data": None, "errors": []}, False),
    ],
    ids=[
        "client_error_no_data_no_path",
        "client_error_missing_data_key",
        "server_error_has_path",
        "server_error_has_partial_data",
        "no_errors",
        "empty_errors_array",
    ],
)
def test_is_client_error(payload, expected):
    assert is_client_error(payload) == expected


def test_server_error(ctx):
    @strawberry.type
    class Author:
        name: str

    @strawberry.type
    class Query:
        @strawberry.field
        def showBug1(self, name: str) -> Author:
            raise ZeroDivisionError("Hidden 1 / 0 bug")

        @strawberry.field
        def showBug2(self, name: str) -> Author:
            raise AssertionError("Another bug")

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query))
    schema = schemathesis.graphql.from_url(api.schema_url)

    @given(case=schema["Query"]["showBug1"].as_strategy())
    @settings(max_examples=1, deadline=None, phases=[Phase.generate])
    def test(case):
        case.call_and_validate()

    with pytest.raises(FailureGroup) as exc:
        test()
    assert "Hidden 1 / 0 bug" in str(exc.value.exceptions[0])


def test_multiple_server_error():
    payload = {
        "data": None,
        "errors": [
            {"message": "Hidden 1 / 0 bug", "locations": [{"line": 2, "column": 3}], "path": ["showBug1"]},
            {"message": "Another bug", "locations": [{"line": 2, "column": 3}], "path": ["showBug2"]},
            {"message": "Third bug", "path": ["showBug2"]},
        ],
    }
    with pytest.raises(Failure, match="GraphQL server error") as exc:
        validate_graphql_response(Mock(operation=Mock(label="GET/ foo")), payload)

    assert exc.value.message == "1. Hidden 1 / 0 bug\n\n2. Another bug\n\n3. Third bug"


def test_no_query(ctx):
    # When GraphQL schema does not contain the `Query` type
    api = ctx.graphql.apps.books()
    response = requests.post(api.schema_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    raw_schema["__schema"]["queryType"] = None
    raw_schema["__schema"]["mutationType"] = None
    schema = schemathesis.graphql.from_dict(raw_schema)
    # Then no operations should be collected
    assert list(schema.get_all_operations()) == []
    assert schema.statistic.operations.total == 0


@pytest.mark.parametrize("with_data_key", [True, False])
def test_data_key(ctx, with_data_key):
    api = ctx.graphql.apps.books()
    response = requests.post(api.schema_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    if not with_data_key:
        decoded = decoded["data"]
    schema = schemathesis.graphql.from_dict(decoded)
    assert schema.statistic.operations.total == 4


def test_malformed_response(ctx):
    api = ctx.graphql.apps.books()
    response = requests.post(api.schema_url, json={"query": get_introspection_query()}, timeout=1)
    response._content += b"42"
    with pytest.raises(LoaderError, match="Received unsupported content while expecting a JSON payload for GraphQL"):
        extract_schema_from_response(response, lambda r: r.json())


def test_operations_count(ctx):
    api = ctx.graphql.apps.books()
    response = requests.post(api.schema_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    schema = schemathesis.graphql.from_dict(raw_schema)
    assert schema.statistic.operations.total == 4


CUSTOM_QUERY_NAME = "MyQuery"
CUSTOM_MUTATION_NAME = "MyMutation"


@pytest.mark.parametrize("name", [CUSTOM_QUERY_NAME, CUSTOM_MUTATION_NAME])
def test_type_names(name):
    # When the user gives custom names to query types
    raw_schema = f"""
    schema {{
       query: {CUSTOM_QUERY_NAME}
       mutation: {CUSTOM_MUTATION_NAME}
    }}

    type {CUSTOM_QUERY_NAME} {{
       v: String
    }}
    type {CUSTOM_MUTATION_NAME} {{
       v(i: Int): String
    }}
    """
    # Then the schema should be loaded without errors
    schema = schemathesis.graphql.from_file(raw_schema)
    # And requests should be properly generated

    @given(case=schema[name]["v"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        pass

    test()


@pytest.mark.parametrize(
    ("schema", "extension"),
    [
        (
            """
type Query {
  func(created: Unknown!): Int!
}""",
            ".gql",
        ),
        (
            """
type Query {
  123(created: Int!): Int!
}""",
            ".whatever",
        ),
    ],
)
def test_schema_error(ctx, testdir, cli, snapshot_cli, schema, extension):
    schema_file = testdir.make_graphql_schema_file(schema, extension=extension)
    api = ctx.graphql.apps.books()
    assert cli.run(str(schema_file), f"--url={api.schema_url}") == snapshot_cli


@pytest.mark.parametrize(
    "arg",
    [
        "--include-name=Query.getBooks",
        "--exclude-name=Query.getBooks",
        "--include-name=DoesNotExist",
    ],
)
def test_filter_operations(ctx, cli, snapshot_cli, arg):
    api = ctx.graphql.apps.books()
    assert cli.run(api.schema_url, "--max-examples=1", "--mode=positive", arg) == snapshot_cli


def test_disallow_null(ctx, cli, testdir, snapshot_cli):
    schema = """type Query {
    getValue(value: Int): Int
}
"""
    schema_file = testdir.make_graphql_schema_file(schema, extension=".gql")
    module = ctx.write_pymodule(
        """
import schemathesis

@schemathesis.hook
def filter_body(context, body):
    node = body.definitions[0].selection_set.selections[0]
    assert node.arguments[0].value.__class__.__name__ != "NullValueNode"
    return True
"""
    )
    api = ctx.graphql.apps.books()
    assert (
        cli.main(
            "run",
            str(schema_file),
            f"--url={api.schema_url}",
            "--generation-graphql-allow-null=false",
            hooks=module,
        )
        == snapshot_cli
    )


def test_unknown_type_name(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    with pytest.raises(LookupError, match="`Qwery` type not found. Did you mean `Query`?"):
        schema["Qwery"]["getBooks"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("getBookz", "`getBookz` field not found. Did you mean `getBooks`?"),
        ("abcdef", "`abcdef` field not found"),
    ],
)
def test_unknown_field_name(ctx, name, expected):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    with pytest.raises(LookupError, match=expected):
        schema["Query"][name]


def test_field_map_operations(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    assert len(schema["Query"]) == 2
    assert list(iter(schema["Query"])) == ["getBooks", "getAuthors"]
    assert schema.find_operation_by_label("Query.getBooks") is not None
    assert schema.find_operation_by_label("Query.getBookz") is None
    assert schema.find_operation_by_label("getBookz") is None


def test_repr(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    assert repr(schema) == "<GraphQLSchema>"


@pytest.mark.parametrize("type_name", ["Query", "Mutation"])
def test_type_as_strategy(ctx, type_name):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    operations = schema[type_name]
    strategy = operations.as_strategy()
    for operation in operations.values():
        # All fields should be possible to generate
        # Note: Phase.explain excluded due to Hypothesis 6.149.0 bug with variable-length strategies
        find(
            strategy,
            lambda x, op=operation: op.definition.field_name in x.body,
            settings=settings(phases=[Phase.generate, Phase.shrink]),
        )


def test_schema_as_strategy(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    strategy = schema.as_strategy()
    for operations in schema.values():
        for operation in operations.values():
            # All fields should be possible to generate
            # Note: Phase.explain excluded due to Hypothesis 6.149.0 bug with variable-length strategies
            find(
                strategy,
                lambda x, op=operation: op.definition.field_name in x.body,
                settings=settings(phases=[Phase.generate, Phase.shrink]),
            )


@pytest.mark.parametrize(
    "check",
    [use_after_free, ensure_resource_availability, ignored_auth, positive_data_acceptance, negative_data_rejection],
)
def test_ignored_checks(ctx, check):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    # Just in case
    case = schema["Query"]["getBooks"].Case()
    assert check(None, None, case)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_mode_cli(ctx, cli, snapshot_cli):
    # Test that negative mode generates invalid queries for mutations with required arguments
    api = ctx.graphql.apps.books()
    assert cli.run(api.schema_url, "--max-examples=1", "--mode=negative") == snapshot_cli


def test_negative_mode_skip_when_impossible(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    operation = schema["Query"]["getBooks"]
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    with pytest.raises(SkipTest, match="Impossible to generate negative test cases"):

        @given(strategy)
        @settings(max_examples=1)
        def test_(case):
            pass

        test_()


def test_negative_mode_fallback_to_positive(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    operation = schema["Query"]["getBooks"]
    schema.config.generation.update(modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE])
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(strategy)
    @settings(max_examples=1)
    def test_(case):
        assert "getBooks" in case.body
        assert case.meta.generation.mode == GenerationMode.POSITIVE

    test_()


def _make_graphql_case_with_mode(schema, mode):
    operation = schema["Mutation"]["addBook"]
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={ParameterLocation.BODY: ComponentInfo(mode=mode)},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description="Negative test case" if mode == GenerationMode.NEGATIVE else "Positive test case",
                parameter=None,
                parameter_location=ParameterLocation.BODY,
                location=None,
            ),
        ),
    )
    return Case(
        operation=operation,
        method="POST",
        path="/graphql",
        body='{ addBook(title: "test", author: "test") { id } }',
        media_type="application/json",
        meta=meta,
    )


def _make_mock_response(content, status_code=200):
    response = requests.Response()
    response._content = json.dumps(content).encode("utf-8")
    response.status_code = status_code
    response.headers["Content-Type"] = "application/json"
    response.request = requests.PreparedRequest()
    response.request.prepare(method="POST", url="http://127.0.0.1/graphql")
    return Response.from_requests(response, True)


def test_not_a_server_error_graphql_negative_mode_accepted_invalid_data(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response({"data": {"addBook": {"id": "1"}}})
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    with pytest.raises(AcceptedNegativeData, match="Invalid data should have been rejected"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_client_error_passes(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    result = not_a_server_error(check_ctx, response, case)
    assert result is None


def test_not_a_server_error_graphql_positive_mode_client_error_raises(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = _make_graphql_case_with_mode(schema, GenerationMode.POSITIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    with pytest.raises(GraphQLClientError, match="Field 'addBook' argument 'title' is required"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_server_error_raises(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Internal error in resolver", "path": ["addBook"]}]}
    )
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    with pytest.raises(GraphQLServerError, match="Internal error in resolver"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_includes_description(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response({"data": {"addBook": {"id": "1"}}})
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    with pytest.raises(AcceptedNegativeData) as exc_info:
        not_a_server_error(check_ctx, response, case)

    assert "Negative test case" in exc_info.value.message


def test_not_a_server_error_graphql_no_meta_falls_through_to_validation(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    case = schema["Mutation"]["addBook"].Case()
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None)

    # Without meta, should fall through to normal validation and raise GraphQLClientError
    with pytest.raises(GraphQLClientError):
        not_a_server_error(check_ctx, response, case)
