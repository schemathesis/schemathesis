"""GraphQL specific loader behavior."""
from io import StringIO

import pytest

from schemathesis.specs.graphql import loaders

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


def test_graphql_asgi_loader(graphql_path, fastapi_graphql_app, run_asgi_test):
    # When an ASGI app is loaded via `from_asgi`
    schema = loaders.from_asgi(graphql_path, fastapi_graphql_app)
    strategy = schema[graphql_path]["POST"].as_strategy()
    # Then it should successfully make calls via `call_asgi`
    run_asgi_test(strategy)


def test_graphql_wsgi_loader(graphql_path, graphql_app, run_wsgi_test):
    # When a WSGI app is loaded via `from_wsgi`
    schema = loaders.from_wsgi(graphql_path, graphql_app)
    strategy = schema[graphql_path]["POST"].as_strategy()
    # Then it should successfully make calls via `call_wsgi`
    run_wsgi_test(strategy)


def defines_type(parsed, name):
    return len([item for item in parsed["__schema"]["types"] if item["name"] == name]) == 1


def assert_schema(schema):
    assert "__schema" in schema.raw_schema
    assert defines_type(schema.raw_schema, "Author")
    assert defines_type(schema.raw_schema, "Book")


@pytest.mark.parametrize("transform", (lambda x: x, StringIO))
def test_graphql_file_loader(transform):
    raw_schema = transform(RAW_SCHEMA)
    schema = loaders.from_file(raw_schema)
    assert_schema(schema)


def test_graphql_path_loader(tmp_path):
    path = tmp_path / "schema.graphql"
    path.write_text(RAW_SCHEMA)
    schema = loaders.from_path(path)
    assert_schema(schema)
