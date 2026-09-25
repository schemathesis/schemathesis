import json
from unittest import SkipTest
from unittest.mock import ANY

import pytest
import requests
import strawberry
from graphql import GraphQLError
from hypothesis import HealthCheck, Phase, find, given, settings

import schemathesis
from schemathesis.checks import CheckContext, not_a_server_error
from schemathesis.config import ChecksConfig
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import LoaderError
from schemathesis.core.failures import AcceptedNegativeData, Failure, FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import USER_AGENT, CallOutcome, Response
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
from schemathesis.graphql.checks import GraphQLClientError, GraphQLSchemaViolation, GraphQLServerError
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


def _books_schema(ctx):
    return schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)


def test_raw_schema(ctx):
    schema = _books_schema(ctx)
    assert schema.specification.name == "GraphQL"


def test_tags(ctx):
    schema = _books_schema(ctx)
    assert schema["Query"]["getBooks"].tags is None


@pytest.mark.hypothesis_nested
def test_operation_strategy(ctx):
    schema = _books_schema(ctx)
    strategy = schema["Query"]["getBooks"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def test(case):
        response = case.call()
        assert response.status_code < 500

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_wsgi_kwargs(ctx):
    schema = _books_schema(ctx)
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
    schema = _books_schema(ctx)
    schema.config.update(base_url="http://0.0.0.0:1234/something")

    # Then the base path is changed, in this case it is the only available path
    assert schema.base_path == "/something"
    strategy = schema["Query"]["getBooks"].as_strategy()
    case = strategy.example()
    # And all requests should go to the specified URL
    assert case.as_transport_kwargs()["url"] == "http://0.0.0.0:1234/something"


@pytest.mark.parametrize("loader", ["from_file", "from_path"])
def test_explicit_base_url_for_schema_loaded_from_file(ctx, tmp_path, loader):
    sdl = "type Query { hello: String }"
    if loader == "from_path":
        path = tmp_path / "schema.graphql"
        path.write_text(sdl)
        schema = schemathesis.graphql.from_path(path)
    else:
        schema = ctx.graphql.load_sdl(sdl)
    case = schema["Query"]["hello"].Case(body="{ hello }")
    assert case.as_transport_kwargs(base_url="http://127.0.0.1:1234/graphql")["url"] == "http://127.0.0.1:1234/graphql"


@pytest.mark.parametrize("kwargs", [{"body": "SomeQuery"}, {"body": b'{"query": "SomeQuery"}'}])
def test_make_case(ctx, kwargs):
    schema = _books_schema(ctx)
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
    schema = _books_schema(ctx)
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
                response_checks=None,
            ),
            response,
            case,
        )


def test_client_error(ctx):
    schema = _books_schema(ctx)
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
        # Apollo caller-side code outweighs the resolver `path`
        (
            {
                "data": None,
                "errors": [{"message": "Bad input", "path": ["addBook"], "extensions": {"code": "BAD_USER_INPUT"}}],
            },
            True,
        ),
        # Apollo server-side code outweighs the missing `path`
        ({"data": None, "errors": [{"message": "Boom", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}]}, False),
        # graphql-java caller-side classification outweighs the resolver `path`
        (
            {
                "data": None,
                "errors": [
                    {"message": "Bad query", "path": ["addBook"], "extensions": {"classification": "ValidationError"}}
                ],
            },
            True,
        ),
        # graphql-java server-side classification outweighs the missing `path`
        (
            {"data": None, "errors": [{"message": "Boom", "extensions": {"classification": "DataFetchingException"}}]},
            False,
        ),
        # Unrecognised markers fall back to the response shape
        ({"data": None, "errors": [{"message": "Nope", "extensions": {"code": "TEAPOT"}}]}, True),
        (
            {"data": None, "errors": [{"message": "Nope", "path": ["addBook"], "extensions": {"code": "TEAPOT"}}]},
            False,
        ),
        (
            {
                "data": None,
                "errors": [
                    {"message": "APQ off", "path": ["addBook"], "extensions": {"code": "PERSISTED_QUERY_NOT_SUPPORTED"}}
                ],
            },
            True,
        ),
        (
            {
                "data": None,
                "errors": [
                    {
                        "message": "No such operation",
                        "path": ["addBook"],
                        "extensions": {"code": "OPERATION_RESOLUTION_FAILURE"},
                    }
                ],
            },
            True,
        ),
        (
            {
                "data": None,
                "errors": [
                    {
                        "message": "Wrongly returned null",
                        "extensions": {"classification": "NullValueInNonNullableField"},
                    }
                ],
            },
            False,
        ),
        (
            {
                "data": None,
                "errors": [
                    {
                        "message": "Mutations are not supported",
                        "path": ["addBook"],
                        "extensions": {"classification": "OperationNotSupported"},
                    }
                ],
            },
            True,
        ),
        # Auth codes say nothing about the data that was sent
        (
            {
                "data": None,
                "errors": [{"message": "Nope", "path": ["addBook"], "extensions": {"code": "UNAUTHENTICATED"}}],
            },
            False,
        ),
        (
            {"data": None, "errors": [{"message": "Nope", "extensions": {"code": "FORBIDDEN"}}]},
            True,
        ),
        # A crash anywhere in the response outweighs a rejection reported beside it
        (
            {
                "data": None,
                "errors": [
                    {"message": "Bad input", "extensions": {"code": "BAD_USER_INPUT"}},
                    {"message": "Boom", "path": ["addBook"], "extensions": {"code": "INTERNAL_SERVER_ERROR"}},
                ],
            },
            False,
        ),
        # Error entries that are not objects must not crash the classification
        ({"data": None, "errors": ["Boom"]}, True),
    ],
    ids=[
        "client_error_no_data_no_path",
        "client_error_missing_data_key",
        "server_error_has_path",
        "server_error_has_partial_data",
        "no_errors",
        "empty_errors_array",
        "apollo_caller_side_code",
        "apollo_server_side_code",
        "graphql_java_caller_side_classification",
        "graphql_java_server_side_classification",
        "unknown_code_without_path",
        "unknown_code_with_path",
        "apollo_persisted_query_not_supported",
        "apollo_operation_resolution_failure",
        "graphql_java_non_null_violation",
        "graphql_java_operation_not_supported",
        "auth_code_with_path_falls_back_to_shape",
        "auth_code_without_path_falls_back_to_shape",
        "server_side_marker_outweighs_caller_side_one",
        "non_object_error_entry",
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


def test_multiple_server_error(ctx):
    schema = _books_schema(ctx)
    case = schema["Mutation"]["addBook"].Case()
    payload = {
        "data": None,
        "errors": [
            {"message": "Hidden 1 / 0 bug", "locations": [{"line": 2, "column": 3}], "path": ["showBug1"]},
            {"message": "Another bug", "locations": [{"line": 2, "column": 3}], "path": ["showBug2"]},
            {"message": "Third bug", "path": ["showBug2"]},
        ],
    }
    with pytest.raises(Failure, match="GraphQL server error") as exc:
        validate_graphql_response(case, payload)

    assert exc.value.message == "1. Hidden 1 / 0 bug\n\n2. Another bug\n\n3. Third bug"


GRAPHQL_CORE_NON_NULL_ERROR = "Cannot return null for non-nullable field Query.getBooks."
GRAPHQL_JAVA_NON_NULL_ERROR = (
    "The field at path '/getBooks' was declared as a non null type, but the code involved in retrieving data has "
    "wrongly returned a null value.  The graphql specification requires that the parent field be set to null, or if "
    "that is non nullable that it bubble up null to its parent and so on. The non-nullable type is 'Book' within "
    "parent type 'Query'"
)


@pytest.mark.parametrize(
    "error_message",
    [GRAPHQL_CORE_NON_NULL_ERROR, GRAPHQL_JAVA_NON_NULL_ERROR],
    ids=["graphql-core", "graphql-java"],
)
def test_schema_violation(ctx, error_message):
    case = _books_schema(ctx)["Query"]["getBooks"].Case()
    with pytest.raises(GraphQLSchemaViolation, match="GraphQL schema violation"):
        validate_graphql_response(case, {"data": None, "errors": [{"message": error_message, "path": ["getBooks"]}]})


def test_schema_violation_from_classification(ctx):
    # Servers that label the error say so in a language the message text cannot be relied on to carry.
    case = _books_schema(ctx)["Query"]["getBooks"].Case()
    payload = {
        "data": None,
        "errors": [
            {
                "message": "Le champ ne peut pas etre null",
                "path": ["getBooks"],
                "extensions": {"classification": "NullValueInNonNullableField"},
            }
        ],
    }
    with pytest.raises(GraphQLSchemaViolation, match="GraphQL schema violation"):
        validate_graphql_response(case, payload)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"data": None, "errors": [{"message": "Cannot read property 'name' of null", "path": ["getBooks"]}]},
            GraphQLServerError,
        ),
        (
            {
                "data": None,
                "errors": [
                    {"message": GRAPHQL_CORE_NON_NULL_ERROR, "path": ["getBooks"]},
                    {"message": "Hidden 1 / 0 bug", "path": ["getAuthors"]},
                ],
            },
            GraphQLServerError,
        ),
        (
            {
                "data": None,
                "errors": [
                    {
                        "message": "Boom",
                        "path": ["getBooks"],
                        "extensions": {"classification": "DataFetchingException"},
                    }
                ],
            },
            GraphQLServerError,
        ),
        ({"errors": [{"message": "Cannot query field 'nope' on type 'Query'."}]}, GraphQLClientError),
    ],
    ids=[
        "resolver_error_mentioning_null",
        "mixed_with_resolver_error",
        "resolver_crash_classification",
        "unknown_field",
    ],
)
def test_not_a_schema_violation(ctx, payload, expected):
    case = _books_schema(ctx)["Query"]["getBooks"].Case()
    with pytest.raises(expected):
        validate_graphql_response(case, payload)


def test_schema_violation_on_real_server(ctx):
    @strawberry.type
    class Query:
        @strawberry.field
        def author_name(self) -> str:
            return None

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query))
    schema = schemathesis.graphql.from_url(api.schema_url)

    @given(case=schema["Query"]["authorName"].as_strategy())
    @settings(max_examples=1, deadline=None, phases=[Phase.generate])
    def test(case):
        case.call_and_validate()

    with pytest.raises(FailureGroup) as exc:
        test()
    assert isinstance(exc.value.exceptions[0], GraphQLSchemaViolation)


def test_no_query(ctx):
    # When GraphQL schema does not contain the `Query` type
    api = ctx.graphql.apps.books()
    response = requests.post(api.schema_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    raw_schema["__schema"]["queryType"] = None
    raw_schema["__schema"]["mutationType"] = None
    schema = ctx.graphql.load_introspection(raw_schema)
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
    schema = ctx.graphql.load_introspection(decoded)
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
    schema = ctx.graphql.load_introspection(raw_schema)
    assert schema.statistic.operations.total == 4


def test_federation_infrastructure_is_not_tested(ctx):
    api = ctx.graphql.apps.federated_subgraph()
    schema = schemathesis.graphql.from_url(api.schema_url)
    assert [operation.ok().label for operation in schema.get_all_operations()] == ["Query.getBooks"]
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (1, 1)


def test_federation_infrastructure_is_tested_when_selected(ctx):
    api = ctx.graphql.apps.federated_subgraph()
    schema = schemathesis.graphql.from_url(api.schema_url).include(name="Query._entities")
    assert [operation.ok().label for operation in schema.get_all_operations()] == ["Query._entities"]
    assert (schema.statistic.operations.total, schema.statistic.operations.selected) == (2, 1)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_federated_subgraph_cli(ctx, cli, snapshot_cli):
    api = ctx.graphql.apps.federated_subgraph()
    assert cli.run(api.schema_url, "--max-examples=5") == snapshot_cli


CUSTOM_QUERY_NAME = "MyQuery"
CUSTOM_MUTATION_NAME = "MyMutation"


@pytest.mark.parametrize("name", [CUSTOM_QUERY_NAME, CUSTOM_MUTATION_NAME])
def test_type_names(ctx, name):
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
    schema = ctx.graphql.load_sdl(raw_schema)
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
    schema = _books_schema(ctx)
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
    schema = _books_schema(ctx)
    with pytest.raises(LookupError, match=expected):
        schema["Query"][name]


def test_field_map_operations(ctx):
    schema = _books_schema(ctx)
    assert len(schema["Query"]) == 2
    assert list(iter(schema["Query"])) == ["getBooks", "getAuthors"]
    assert schema.find_operation_by_label("Query.getBooks") is not None
    assert schema.find_operation_by_label("Query.getBookz") is None
    assert schema.find_operation_by_label("getBookz") is None


def test_repr(ctx):
    schema = _books_schema(ctx)
    assert repr(schema) == "<GraphQLSchema>"


@pytest.mark.parametrize("type_name", ["Query", "Mutation"])
def test_type_as_strategy(ctx, type_name):
    schema = _books_schema(ctx)
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
    schema = _books_schema(ctx)
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
    schema = _books_schema(ctx)
    # Just in case
    case = schema["Query"]["getBooks"].Case()
    assert check(None, None, case)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_mode_cli(ctx, cli, snapshot_cli):
    # Test that negative mode generates invalid queries for mutations with required arguments
    api = ctx.graphql.apps.books()
    assert cli.run(api.schema_url, "--max-examples=1", "--mode=negative") == snapshot_cli


def test_negative_mode_skip_when_impossible(ctx):
    schema = _books_schema(ctx)
    operation = schema["Query"]["getBooks"]
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    with pytest.raises(SkipTest, match="Impossible to generate negative test cases"):

        @given(strategy)
        @settings(max_examples=1, suppress_health_check=list(HealthCheck))
        def test_(case):
            pass

        test_()


def test_negative_mode_fallback_to_positive(ctx):
    schema = _books_schema(ctx)
    operation = schema["Query"]["getBooks"]
    schema.config.generation.update(modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE])
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(strategy)
    @settings(max_examples=1, suppress_health_check=list(HealthCheck))
    def test_(case):
        assert "getBooks" in case.body
        assert case.meta.generation.mode == GenerationMode.POSITIVE

    test_()


def _make_graphql_case_with_mode(
    schema, mode, *, operation=None, body='{ addBook(title: "test", author: "test") { id } }'
):
    operation = operation if operation is not None else schema["Mutation"]["addBook"]
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
        body=body,
        media_type="application/json",
        meta=meta,
    )


def _make_mock_response(content, status_code=200, content_type="application/json"):
    response = requests.Response()
    response._content = json.dumps(content).encode("utf-8")
    response.status_code = status_code
    response.headers["Content-Type"] = content_type
    # Derive `encoding` from headers exactly like requests' adapter does for real responses.
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    response.request = requests.PreparedRequest()
    response.request.prepare(method="POST", url="http://127.0.0.1/graphql")
    return Response.from_requests(response, True)


def test_not_a_server_error_graphql_negative_mode_accepted_invalid_data(ctx):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response({"data": {"addBook": {"id": "1"}}})
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    with pytest.raises(AcceptedNegativeData, match="Invalid data should have been rejected"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_client_error_passes(ctx):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    result = not_a_server_error(check_ctx, response, case)
    assert result is None


def test_not_a_server_error_graphql_positive_mode_client_error_raises(ctx):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.POSITIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    with pytest.raises(GraphQLClientError, match="Field 'addBook' argument 'title' is required"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_server_error_raises(ctx):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Internal error in resolver", "path": ["addBook"]}]}
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    with pytest.raises(GraphQLServerError, match="Internal error in resolver"):
        not_a_server_error(check_ctx, response, case)


def test_negative_mode_resolver_rejection_marked_by_extensions(ctx):
    # A resolver that rejects bad input reports a `path`, so only its `extensions` tell a rejection from a crash.
    @strawberry.type
    class Book:
        title: str

    @strawberry.type
    class Query:
        @strawberry.field
        def bookByTitle(self, title: str) -> Book:
            raise GraphQLError("Title must not be empty", extensions={"code": "BAD_USER_INPUT"})

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query))
    schema = schemathesis.graphql.from_url(api.schema_url)
    case = _make_graphql_case_with_mode(
        schema,
        GenerationMode.NEGATIVE,
        operation=schema["Query"]["bookByTitle"],
        body='{ bookByTitle(title: "") { title } }',
    )

    assert case.call_and_validate().json()["errors"][0]["path"] == ["bookByTitle"]


def test_not_a_server_error_graphql_positive_mode_server_side_extensions_code_raises(ctx, response_factory):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.POSITIVE)
    response = response_factory.requests(
        content=json.dumps(
            {
                "data": None,
                "errors": [
                    {
                        "message": "Internal error in resolver",
                        "path": ["addBook"],
                        "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                    }
                ],
            }
        ).encode()
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    with pytest.raises(GraphQLServerError, match="Internal error in resolver"):
        not_a_server_error(check_ctx, response, case)


def test_not_a_server_error_graphql_negative_mode_includes_description(ctx):
    schema = _books_schema(ctx)
    case = _make_graphql_case_with_mode(schema, GenerationMode.NEGATIVE)
    response = _make_mock_response({"data": {"addBook": {"id": "1"}}})
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    with pytest.raises(AcceptedNegativeData) as exc_info:
        not_a_server_error(check_ctx, response, case)

    assert "Negative test case" in exc_info.value.message


def test_not_a_server_error_graphql_no_meta_falls_through_to_validation(ctx):
    schema = _books_schema(ctx)
    case = schema["Mutation"]["addBook"].Case()
    response = _make_mock_response(
        {"data": None, "errors": [{"message": "Field 'addBook' argument 'title' is required"}]}
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    # Without meta, should fall through to normal validation and raise GraphQLClientError
    with pytest.raises(GraphQLClientError):
        not_a_server_error(check_ctx, response, case)


@pytest.mark.parametrize(
    "charset", ["bogus-xyz", "undefined", "ab\x00cd"], ids=["unknown-charset", "undefined-codec", "nul-in-charset"]
)
def test_not_a_server_error_graphql_bad_charset(ctx, charset):
    # A response lying about its charset over valid GraphQL JSON must validate normally, not crash.
    schema = _books_schema(ctx)
    case = schema["Mutation"]["addBook"].Case()
    response = _make_mock_response(
        {"data": {"addBook": {"id": "1"}}}, content_type=f"application/json; charset={charset}"
    )
    check_ctx = CheckContext(
        override=None, auth=None, headers=None, config=ChecksConfig(), transport_kwargs=None, response_checks=None
    )

    assert not_a_server_error(check_ctx, response, case) is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b'{"data": {"getBooks": []}}', CallOutcome.ACCEPTED),
        (b'{"data": null, "errors": [{"message": "Boom", "path": ["getBooks"]}]}', CallOutcome.REJECTED),
        (b'{"data": {"getBooks": []}, "errors": [{"message": "Boom"}]}', CallOutcome.REJECTED),
        (b'{"data": null}', CallOutcome.REJECTED),
        (b'{"data": {"getBooks": []}, "errors": []}', CallOutcome.ACCEPTED),
        (b"INTERNAL SERVER ERROR", CallOutcome.UNINFORMATIVE),
    ],
    ids=["data", "errors", "partial-data", "null-data", "empty-errors", "not-a-graphql-response"],
)
def test_classify_call_outcome(ctx, response_factory, content, expected):
    schema = _books_schema(ctx)
    assert schema.classify_call_outcome(response_factory.requests(content=content)) is expected


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_classify_call_outcome_ignores_uninformative_responses(ctx, response_factory, status_code):
    schema = _books_schema(ctx)
    response = response_factory.requests(status_code=status_code, content=b'{"errors": [{"message": "Nope"}]}')
    assert schema.classify_call_outcome(response) is CallOutcome.UNINFORMATIVE
