"""GraphQL specific loader behavior."""

import json
from io import StringIO

import graphql
import pytest
from flask import Flask, Response
from hypothesis import given, settings

from schemathesis.core.errors import LoaderError
from schemathesis.graphql import loaders
from schemathesis.transport.prepare import normalize_base_url

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

    @given(case=strategy)
    @settings(max_examples=1, deadline=None)
    def test(case):
        assert (
            case.as_transport_kwargs(base_url=normalize_base_url(case.operation.base_url))["url"]
            == "http://localhost/graphql/"
        )

    test()


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


def test_graphql_path_loader(tmp_path):
    path = tmp_path / "schema.graphql"
    path.write_text(RAW_SCHEMA)
    schema = loaders.from_path(path)
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
