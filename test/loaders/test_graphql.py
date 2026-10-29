"""GraphQL specific loader behavior."""

import json
from io import StringIO

import graphql
import pytest
import strawberry
from flask import Flask, Response, jsonify, request

from schemathesis.core.errors import LoaderError
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.graphql import loaders
from schemathesis.transport.prepare import normalize_base_url
from test.utils import run_given

RAW_SCHEMA = """
type Book {
  title: String
  author: Author
}

type Author {
  name: String
  books: [Book]
}

type Query {
  getBooks: [Book]
  getAuthors: [Author]
}"""


def test_graphql_asgi_loader(ctx, run_test):
    api = ctx.graphql.apps.books(framework="fastapi")
    # When an ASGI app is loaded via `from_asgi`
    schema = loaders.from_asgi("/graphql", api.wsgi_app)
    strategy = schema["Query"]["getBooks"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


def test_graphql_wsgi_loader(ctx, run_test):
    api = ctx.graphql.apps.books()
    # When a WSGI app is loaded via `from_wsgi`
    schema = loaders.from_wsgi("/graphql", api.wsgi_app)
    strategy = schema["Query"]["getBooks"].as_strategy()
    # Then it should successfully make calls
    run_test(strategy)


def test_graphql_url(ctx):
    api = ctx.graphql.apps.books(framework="fastapi")
    # See GH-1987
    schema = loaders.from_asgi("/graphql", api.wsgi_app)
    schema.location = "/graphql/"
    strategy = schema["Query"]["getBooks"].as_strategy()

    @run_given(case=strategy, max_examples=1)
    def test(case):
        assert (
            case.as_transport_kwargs(base_url=normalize_base_url(case.operation.base_url))["url"]
            == "http://localhost/graphql/"
        )


def defines_type(parsed, name):
    return len([item for item in parsed["__schema"]["types"] if item["name"] == name]) == 1


def assert_schema(schema):
    assert "__schema" in schema.raw_schema
    assert defines_type(schema.raw_schema, "Author")
    assert defines_type(schema.raw_schema, "Book")


@pytest.mark.parametrize("transform", [lambda x: x, StringIO])
def test_graphql_file_loader(transform):
    raw_schema = transform(RAW_SCHEMA)
    schema = loaders.from_file(raw_schema)
    assert_schema(schema)


def test_graphql_path_loader(ctx):
    schema = loaders.from_path(ctx.graphql.write_sdl(RAW_SCHEMA))
    assert_schema(schema)


def test_from_json_file(tmp_path):
    document = graphql.build_schema(RAW_SCHEMA)
    result = graphql.execute(document, loaders.get_introspection_query_ast())
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(result.data))
    schema = loaders.from_path(str(path))
    assert_schema(schema)


@pytest.mark.parametrize("data", ["{}", "[]", "--"])
def test_from_invalid_json_file(tmp_path, data):
    path = tmp_path / "schema.json"
    path.write_text(data)
    with pytest.raises(LoaderError, match="The provided API schema does not appear to be a valid GraphQL schema"):
        loaders.from_path(str(path))


@pytest.mark.parametrize("charset", ["bogus-xyz", "undefined"])
def test_from_url_survives_bad_charset(app_runner, charset):
    # An introspection response lying about its charset over valid UTF-8 JSON must still load.
    document = graphql.build_schema(RAW_SCHEMA)
    result = graphql.execute(document, loaders.get_introspection_query_ast())
    payload = json.dumps({"data": result.data})
    app = Flask(__name__)

    @app.route("/graphql", methods=["POST"])
    def graphql_endpoint():
        return Response(payload, content_type=f"application/json; charset={charset}")

    assert_schema(loaders.from_url(app_runner.openapi_url(app, path="/graphql")))


ONE_OF_SCHEMA = "input Pick @oneOf { id: ID, name: String } type Query { find(pick: Pick!): Int }"


@strawberry.input(one_of=True)
class Pick:
    id: strawberry.ID | None = strawberry.UNSET
    name: str | None = strawberry.UNSET


@strawberry.type(name="Query")
class OneOfQuery:
    @strawberry.field
    def find(self, pick: Pick) -> int | None:
        return None


def invalid_documents(schema):
    target = graphql.build_schema(ONE_OF_SCHEMA)
    documents = [case.body for case in generate_many(schema["Query"]["find"].as_strategy(), 30)]
    return [document for document in documents if graphql.validate(target, graphql.parse(document))]


# A Strawberry schema in the same process makes graphql-core report `isOneOf: false` for any other schema.
@pytest.mark.parametrize("loader", ["file", "url", "wsgi", "asgi"])
def test_one_of_inputs_get_one_field(ctx, loader):
    strawberry_schema = strawberry.Schema(query=OneOfQuery)
    if loader == "file":
        schema = loaders.from_file(ONE_OF_SCHEMA)
    elif loader == "url":
        schema = loaders.from_url(ctx.graphql.apps.from_schema(strawberry_schema).schema_url)
    elif loader == "wsgi":
        schema = loaders.from_wsgi("/graphql", ctx.graphql.apps.from_schema(strawberry_schema).wsgi_app)
    else:
        api = ctx.graphql.apps.from_schema(strawberry_schema, framework="fastapi")
        schema = loaders.from_asgi("/graphql", api.wsgi_app)
    assert invalid_documents(schema) == []


def introspection_app(queries, status):
    # Answers like a server predating `@oneOf`, which rejects the field in the introspection query.
    schema = graphql.build_schema(ONE_OF_SCHEMA)
    app = Flask(__name__)

    @app.route("/graphql", methods=["POST"])
    def graphql_endpoint():
        query = request.json["query"]
        queries.append(query)
        if "isOneOf" in query:
            return jsonify(errors=[{"message": 'Cannot query field "isOneOf" on type "__Type".'}]), status
        return jsonify(data=graphql.graphql_sync(schema, query).data)

    return app


@pytest.mark.parametrize("status", [200, 400])
def test_introspection_without_one_of_support(app_runner, status):
    queries = []
    schema = loaders.from_url(app_runner.openapi_url(introspection_app(queries, status), path="/graphql"))
    assert defines_type(schema.raw_schema, "Pick")
    assert ["isOneOf" in query for query in queries] == [True, False]


def test_introspection_server_error_is_not_retried(app_runner):
    queries = []
    with pytest.raises(LoaderError, match="HTTP 500"):
        loaders.from_url(app_runner.openapi_url(introspection_app(queries, 500), path="/graphql"))
    assert len(queries) == 1


def test_custom_introspection_query_is_sent_once(app_runner):
    queries = []
    url = app_runner.openapi_url(introspection_app(queries, 400), path="/graphql")
    query = loaders.get_introspection_query(one_of=False)
    schema = loaders.from_url(url, json={"query": query})
    assert defines_type(schema.raw_schema, "Pick")
    assert queries == [query]
