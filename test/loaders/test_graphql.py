"""GraphQL specific loader behavior."""
from schemathesis.specs.graphql import loaders


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
