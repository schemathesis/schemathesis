import logging
from typing import List

import click
from aiohttp import web

from schemathesis.cli import CSVOption

try:
    from . import _aiohttp, _flask
    from .utils import Endpoint
except ImportError:
    import _aiohttp
    import _flask
    from utils import Endpoint


@click.command()
@click.argument("port", type=int)
@click.option("--endpoints", type=CSVOption(Endpoint))
@click.option("--framework", type=click.Choice(["aiohttp", "flask"]), default="aiohttp")
def run_app(port: int, endpoints: List[Endpoint], framework: str) -> None:
    if endpoints is not None:
        prepared_endpoints = tuple(endpoint.name for endpoint in endpoints)
    else:
        prepared_endpoints = tuple(endpoint.name for endpoint in Endpoint)
    if framework == "aiohttp":
        app = _aiohttp.create_app(prepared_endpoints)
        web.run_app(app, port=port)
    elif framework == "flask":
        app = _flask.create_app(prepared_endpoints)
        app.run(port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    run_app()
