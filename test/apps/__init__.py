import logging
from typing import List

import click
from aiohttp import web

from schemathesis.cli import CSVOption

try:
    from . import _aiohttp, _flask, _graphql
    from .utils import Endpoint, OpenAPIVersion
except ImportError as exc:
    # try/except for cases when there is a different ImportError in the block before, that
    # doesn't imply another running environment (test_server.sh vs usual pytest run)
    # Ref: https://github.com/schemathesis/schemathesis/issues/658
    try:
        import _aiohttp
        import _flask
        import _graphql
        from utils import Endpoint, OpenAPIVersion
    except ImportError:
        raise exc


@click.command()
@click.argument("port", type=int)
@click.option("--endpoints", type=CSVOption(Endpoint))
@click.option("--spec", type=click.Choice(["openapi2", "openapi3", "graphql"]), default="openapi2")
@click.option("--framework", type=click.Choice(["aiohttp", "flask"]), default="aiohttp")
def run_app(port: int, endpoints: List[Endpoint], spec: str, framework: str) -> None:
    if spec == "graphql":
        app = _graphql.create_app()
        app.run(port=port)
    else:
        if endpoints is not None:
            prepared_endpoints = tuple(endpoint.name for endpoint in endpoints)
        else:
            prepared_endpoints = tuple(endpoint.name for endpoint in Endpoint)
        version = {"openapi2": OpenAPIVersion("2.0"), "openapi3": OpenAPIVersion("3.0")}[spec]
        if framework == "aiohttp":
            app = _aiohttp.create_openapi_app(prepared_endpoints, version)
            web.run_app(app, port=port)
        elif framework == "flask":
            app = _flask.create_openapi_app(prepared_endpoints, version)
            app.run(port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    run_app()
