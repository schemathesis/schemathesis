"""Local launcher for catalog test apps.

Run a single catalog factory on a chosen port for manual debugging:

    python -m test.apps 8080 success
    python -m test.apps 8080 success_and_failure
    python -m test.apps 8080 users_crud
    python -m test.apps 8080 envelopes.ajv
    python -m test.apps 8080 graphql.books

The first positional argument is the port; the second is the factory name
(defaults to ``success_and_failure``). Use ``--list`` to see all factories.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from functools import partial

from test.apps.catalog import openapi as openapi_catalog
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import envelopes
from test.apps.catalog.openapi.envelopes import Envelope
from test.apps.runtime import OpenAPIApp

# Every catalog module; envelopes take an argument, so they get their own entries below.
_OPENAPI_MODULES = {
    module.name: importlib.import_module(f"{openapi_catalog.__name__}.{module.name}")
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
    "books": graphql_bookstore.books,
    "federated_subgraph": graphql_bookstore.federated_subgraph,
    "use_after_create": graphql_bookstore.use_after_create,
    "tombstone_pool": graphql_bookstore.tombstone_pool,
    "use_after_delete": graphql_bookstore.use_after_delete,
    "slow_mutation": graphql_bookstore.slow_mutation,
    "double_delete": graphql_bookstore.double_delete,
    "generic_id_pool": graphql_bookstore.generic_id_pool,
    "input_object_pool": graphql_bookstore.input_object_pool,
    "list_argument_pool": graphql_bookstore.list_argument_pool,
    "non_id_pool": graphql_bookstore.non_id_pool,
    "bare_slug": graphql_bookstore.bare_slug,
    "relay_connection": graphql_bookstore.relay_connection,
    "lenient": graphql_bookstore.lenient,
    "coverage_inputs": graphql_bookstore.coverage_inputs,
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
    module = _OPENAPI_MODULES.get(module_name)
    if module is None:
        raise SystemExit(f"Unknown module: {module_name!r}. Choices: {sorted(_OPENAPI_MODULES)}")
    factory = getattr(module, factory_name, None)
    if factory is None or not callable(factory):
        raise SystemExit(f"Unknown factory {factory_name!r} in {module_name}")
    return factory


def _list_factories() -> None:
    print("Available factories (default module is `basic`; use `<module>.<name>` for others):")
    for module_name, module in _OPENAPI_MODULES.items():
        names = sorted(
            name
            for name, value in vars(module).items()
            if not name.startswith("_") and callable(value) and getattr(value, "__module__", None) == module.__name__
        )
        if names:
            print(f"\n  {module_name}.*:")
            for name in names:
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
        help="Factory name (e.g. 'success', 'users_crud', 'graphql.books').",
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
