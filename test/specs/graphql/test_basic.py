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
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import USER_AGENT
from schemathesis.graphql.loaders import extract_schema_from_response, get_introspection_query
from schemathesis.specs.graphql.validation import validate_graphql_response
from schemathesis.specs.openapi.checks import (
    ensure_resource_availability,
    ignored_auth,
    negative_data_rejection,
    positive_data_acceptance,
    use_after_free,
)
from schemathesis.transport.prepare import get_default_headers
from schemathesis.transport.wsgi import WSGI_TRANSPORT
from test.apps import _graphql as graphql
from test.apps._graphql.schema import Author
from test.utils import assert_requests_call


def test_raw_schema(graphql_schema):
    assert graphql_schema.specification.name == "GraphQL"


def test_tags(graphql_schema):
    assert graphql_schema["Query"]["getBooks"].tags is None


@pytest.mark.hypothesis_nested
def test_operation_strategy(graphql_strategy):
    @given(case=graphql_strategy)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def test(case):
        response = case.call()
        assert response.status_code < 500

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_wsgi_kwargs(graphql_strategy):
    case = graphql_strategy.example()
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
def test_custom_base_url(graphql_url):
    schema = schemathesis.graphql.from_url(graphql_url)
    schema.config.update(base_url="http://0.0.0.0:1234/something")

    # Then the base path is changed, in this case it is the only available path
    assert schema.base_path == "/something"
    strategy = schema["Query"]["getBooks"].as_strategy()
    case = strategy.example()
    # And all requests should go to the specified URL
    assert case.as_transport_kwargs()["url"] == "http://0.0.0.0:1234/something"


@pytest.mark.parametrize("kwargs", [{"body": "SomeQuery"}, {"body": b'{"query": "SomeQuery"}'}])
def test_make_case(graphql_schema, kwargs):
    case = graphql_schema["Query"]["getBooks"].Case(**kwargs)
    assert_requests_call(case)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"content": b"INTERNAL SERVER ERROR", "content_type": "text/plain"}, "JSON deserialization error"),
        ({"content": b"[]"}, "Unexpected GraphQL Response"),
    ],
)
def test_response_validation(graphql_schema, response_factory, kwargs, expected):
    response = response_factory.requests(status_code=200, **kwargs)
    case = graphql_schema["Query"]["getBooks"].Case(body="Q")
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


def test_client_error(graphql_schema):
    case = graphql_schema["Query"]["getBooks"].Case(body="invalid query")
    with pytest.raises(FailureGroup) as exc:
        case.call_and_validate()
    assert "Syntax Error: Unexpected Name 'invalid'." in str(exc.value.exceptions[0])


def test_server_error(graphql_path, app_runner):
    @strawberry.type
    class Query:
        @strawberry.field
        def showBug1(self, name: str) -> Author:
            raise ZeroDivisionError("Hidden 1 / 0 bug")

        @strawberry.field
        def showBug2(self, name: str) -> Author:
            raise AssertionError("Another bug")

    gql_schema = strawberry.Schema(Query)

    app = graphql._flask.create_app(graphql_path, schema=gql_schema)
    port = app_runner.run_flask_app(app)
    graphql_url = f"http://127.0.0.1:{port}{graphql_path}"
    graphql_schema = schemathesis.graphql.from_url(graphql_url)

    @given(case=graphql_schema["Query"]["showBug1"].as_strategy())
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


def test_no_query(graphql_url):
    # When GraphQL schema does not contain the `Query` type
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    raw_schema["__schema"]["queryType"] = None
    raw_schema["__schema"]["mutationType"] = None
    schema = schemathesis.graphql.from_dict(raw_schema)
    # Then no operations should be collected
    assert list(schema.get_all_operations()) == []
    assert schema.statistic.operations.total == 0


@pytest.mark.parametrize("with_data_key", [True, False])
def test_data_key(graphql_url, with_data_key):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    if not with_data_key:
        decoded = decoded["data"]
    schema = schemathesis.graphql.from_dict(decoded)
    assert schema.statistic.operations.total == 4


def test_malformed_response(graphql_url):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    response._content += b"42"
    with pytest.raises(LoaderError, match="Received unsupported content while expecting a JSON payload for GraphQL"):
        extract_schema_from_response(response, lambda r: r.json())


def test_operations_count(graphql_url):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
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
def test_schema_error(testdir, cli, snapshot_cli, schema, extension, graphql_url):
    schema_file = testdir.make_graphql_schema_file(schema, extension=extension)
    assert cli.run(str(schema_file), f"--url={graphql_url}") == snapshot_cli


@pytest.mark.parametrize("arg", ["--include-name=Query.getBooks", "--exclude-name=Query.getBooks"])
def test_filter_operations(cli, graphql_url, snapshot_cli, arg):
    assert cli.run(graphql_url, "--max-examples=1", arg) == snapshot_cli


def test_disallow_null(ctx, cli, testdir, snapshot_cli, graphql_url):
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
    assert (
        cli.main(
            "run",
            str(schema_file),
            f"--url={graphql_url}",
            "--generation-graphql-allow-null=false",
            hooks=module,
        )
        == snapshot_cli
    )


def test_unknown_type_name(graphql_schema):
    with pytest.raises(LookupError, match="`Qwery` type not found. Did you mean `Query`?"):
        graphql_schema["Qwery"]["getBooks"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("getBookz", "`getBookz` field not found. Did you mean `getBooks`?"),
        ("abcdef", "`abcdef` field not found"),
    ],
)
def test_unknown_field_name(graphql_schema, name, expected):
    with pytest.raises(LookupError, match=expected):
        graphql_schema["Query"][name]


def test_field_map_operations(graphql_schema):
    assert len(graphql_schema["Query"]) == 2
    assert list(iter(graphql_schema["Query"])) == ["getBooks", "getAuthors"]
    assert graphql_schema.find_operation_by_label("Query.getBooks") is not None
    assert graphql_schema.find_operation_by_label("Query.getBookz") is None
    assert graphql_schema.find_operation_by_label("getBookz") is None


def test_repr(graphql_schema):
    assert repr(graphql_schema) == "<GraphQLSchema>"


@pytest.mark.parametrize("type_name", ["Query", "Mutation"])
def test_type_as_strategy(graphql_schema, type_name):
    operations = graphql_schema[type_name]
    strategy = operations.as_strategy()
    for operation in operations.values():
        # All fields should be possible to generate
        find(strategy, lambda x, op=operation: op.definition.field_name in x.body)


def test_schema_as_strategy(graphql_schema):
    strategy = graphql_schema.as_strategy()
    for operations in graphql_schema.values():
        for operation in operations.values():
            # All fields should be possible to generate
            find(strategy, lambda x, op=operation: op.definition.field_name in x.body)


@pytest.mark.parametrize(
    "check",
    [use_after_free, ensure_resource_availability, ignored_auth, positive_data_acceptance, negative_data_rejection],
)
def test_ignored_checks(graphql_schema, check):
    # Just in case
    case = graphql_schema["Query"]["getBooks"].Case()
    assert check(None, None, case)
