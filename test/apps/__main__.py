"""Local launcher for catalog test apps.

Run a single catalog factory on a chosen port for manual debugging:

    python -m test.apps 8080 success
    python -m test.apps 8080 success_and_failure
    python -m test.apps 8080 users.crud
    python -m test.apps 8080 envelopes.ajv
    python -m test.apps 8080 graphql.books

The first positional argument is the port; the second is the factory name
(defaults to ``success_and_failure``). Use ``--list`` to see all factories.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import pkgutil
import sys
from collections.abc import Callable
from functools import partial
from types import ModuleType

from test.apps.catalog import openapi as openapi_catalog
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import envelopes
from test.apps.catalog.openapi.envelopes import Envelope
from test.apps.runtime import GraphQLApp, OpenAPIApp


def _factories(module: ModuleType) -> dict[str, Callable[[], OpenAPIApp | GraphQLApp]]:
    # Factories that need no arguments; the rest get explicit entries.
    return {
        name: value
        for name, value in vars(module).items()
        if inspect.isfunction(value)
        and value.__module__ == module.__name__
        and not name.startswith("_")
        and all(
            parameter.default is not parameter.empty
            or parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
            for parameter in inspect.signature(value).parameters.values()
        )
    }


# Every catalog module; envelopes take an argument, so they get their own entries below.
_OPENAPI_FACTORIES = {
    module.name: _factories(importlib.import_module(f"{openapi_catalog.__name__}.{module.name}"))
    for module in pkgutil.iter_modules(openapi_catalog.__path__)
    if not module.ispkg and module.name != "envelopes"
}

_ENVELOPE_FACTORIES = {
    **{
        name.lower(): partial(envelopes.planted_bug, value)
        for name, value in vars(envelopes).items()
        if isinstance(value, Envelope)
    },
    **{f"rails_{variant}": partial(envelopes.planted_bug, value) for variant, value in envelopes.RAILS.items()},
}

_GRAPHQL_FACTORIES = {
    **_factories(graphql_bookstore),
    # Answers every query with no data, the shape of a server that never serves the query.
    "canned_response": partial(graphql_bookstore.canned_response, {"data": None}),
}


def _resolve(name: str):
    if name.startswith("graphql."):
        factory_name = name.split(".", 1)[1]
        if factory_name not in _GRAPHQL_FACTORIES:
            raise SystemExit(f"Unknown graphql factory: {factory_name!r}. Choices: {sorted(_GRAPHQL_FACTORIES)}")
        return _GRAPHQL_FACTORIES[factory_name]
    if name.startswith("envelopes."):
        factory_name = name.split(".", 1)[1]
        if factory_name not in _ENVELOPE_FACTORIES:
            raise SystemExit(f"Unknown envelope: {factory_name!r}. Choices: {sorted(_ENVELOPE_FACTORIES)}")
        return _ENVELOPE_FACTORIES[factory_name]
    if "." in name:
        module_name, factory_name = name.split(".", 1)
    else:
        module_name, factory_name = "basic", name
    factories = _OPENAPI_FACTORIES.get(module_name)
    if factories is None:
        raise SystemExit(f"Unknown module: {module_name!r}. Choices: {sorted(_OPENAPI_FACTORIES)}")
    factory = factories.get(factory_name)
    if factory is None:
        raise SystemExit(f"Unknown factory {factory_name!r} in {module_name}")
    return factory


def _list_factories() -> None:
    print("Available factories (default module is `basic`; use `<module>.<name>` for others):")
    for module_name, factories in _OPENAPI_FACTORIES.items():
        if factories:
            print(f"\n  {module_name}.*:")
            for name in sorted(factories):
                print(f"    {name}")
    print("\n  envelopes.*:")
    for name in sorted(_ENVELOPE_FACTORIES):
        print(f"    {name}")
    print("\n  graphql.*:")
    for name in sorted(_GRAPHQL_FACTORIES):
        print(f"    {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch a catalog test app on a port.")
    parser.add_argument("port", type=int, nargs="?", help="Port to bind to.")
    parser.add_argument(
        "factory",
        nargs="?",
        default="success_and_failure",
        help="Factory name (e.g. 'success', 'users.crud', 'graphql.books').",
    )
    parser.add_argument("--list", action="store_true", help="List available factories and exit.")
    args = parser.parse_args()

    if args.list or args.port is None:
        _list_factories()
        sys.exit(0)

    factory = _resolve(args.factory)
    app = factory()
    server = app.server
    if isinstance(app, OpenAPIApp) and app.kind == "fastapi":
        import uvicorn

        uvicorn.run(server, host="127.0.0.1", port=args.port)
    else:
        server.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
