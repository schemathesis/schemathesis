from __future__ import annotations

import logging

import click
from aiohttp import web

from schemathesis.cli import CsvEnumChoice

try:
    from . import _graphql, openapi
except ImportError as exc:
    # try/except for cases when there is a different ImportError in the block before, that
    # doesn't imply another running environment (test_server.sh vs usual pytest run)
    # Ref: https://github.com/schemathesis/schemathesis/issues/658
    try:
        import _graphql
        import openapi
    except ImportError:
        raise exc from None


INVALID_OPERATIONS = ("invalid", "invalid_response", "invalid_path_parameter", "missing_path_parameter")
AvailableOperations = CsvEnumChoice(openapi.schema.Operation)


@click.command()
@click.argument("port", type=int)
@click.option("--operations", type=AvailableOperations)
@click.option("--spec", type=click.Choice(["openapi2", "openapi3", "graphql"]), default="openapi2")
@click.option("--framework", type=click.Choice(["aiohttp", "flask"]), default="aiohttp")
def run_app(port: int, operations: list[openapi.schema.Operation], spec: str, framework: str) -> None:
    if spec == "graphql":
        app = _graphql._flask.create_app()
        app.run(port=port)
    else:
        if operations is not None:
            prepared_operations = tuple(operation.name for operation in operations)
            if "all" in prepared_operations:
                prepared_operations = tuple(
                    operation.name for operation in openapi.schema.Operation if operation.name != "all"
                )
        else:
            prepared_operations = tuple(
                operation.name
                for operation in openapi.schema.Operation
                if operation.name not in INVALID_OPERATIONS and operation.name != "all"
            )
        version = {"openapi2": openapi.schema.OpenAPIVersion("2.0"), "openapi3": openapi.schema.OpenAPIVersion("3.0")}[
            spec
        ]
        click.secho(
            f"Schemathesis test server is running!\n\n"
            f"API Schema is available at: http://0.0.0.0:{port}/schema.yaml\n",
            bold=True,
        )
        if framework == "aiohttp":
            app = openapi._aiohttp.create_app(prepared_operations, version)
            web.run_app(app, port=port)
        elif framework == "flask":
            app = openapi._flask.create_app(prepared_operations, version)
            app.run(port=port)


if __name__ == "__main__":
    run_app()
