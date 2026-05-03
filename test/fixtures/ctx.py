from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Literal, overload

import pytest
import yaml
from flask import Flask

from schemathesis.checks import CHECKS
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER
from test.apps import builders
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import basic as openapi_basic
from test.apps.catalog.openapi import error_feedback as openapi_error_feedback
from test.apps.catalog.openapi import laravel as openapi_laravel
from test.apps.catalog.openapi import rails as openapi_rails
from test.apps.catalog.openapi import stateful as openapi_stateful
from test.apps.catalog.openapi import supervisor as openapi_supervisor
from test.apps.catalog.openapi import under_declared_security as openapi_under_declared_security
from test.apps.catalog.openapi import zod as openapi_zod
from test.apps.runtime import GraphQLApp, GraphQLServer, Modifier, OpenAPIApp, OpenAPIServer


@overload
def _start(parent: Context, app: OpenAPIApp) -> OpenAPIServer: ...


@overload
def _start(parent: Context, app: GraphQLApp) -> GraphQLServer: ...


def _start(parent: Context, app: OpenAPIApp | GraphQLApp) -> OpenAPIServer | GraphQLServer:
    app_runner = parent.request.getfixturevalue("app_runner")
    runner = app_runner.run_flask_app if app.kind == "flask" else app_runner.run_asgi_app
    return app.make_server(port=runner(app.server))


@dataclass(slots=True)
class OpenAPIApps:
    parent: Context

    def success(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success())

    def failure(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.failure())

    def multiple_failures(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.multiple_failures())

    def custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.custom_format())

    def flaky(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.flaky())

    def multipart(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.multipart())

    def csv_payload(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.csv_payload())

    def ignored_auth(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.ignored_auth())

    def slow(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.slow())

    def success_and_slow(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_slow())

    def headers(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.headers())

    def path_variable(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.path_variable())

    def payload(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.payload())

    def unsatisfiable(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.unsatisfiable())

    def failure_multiple_failures_unsatisfiable(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.failure_multiple_failures_unsatisfiable())

    def path_variable_and_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.path_variable_and_custom_format())

    def basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.basic())

    def success_and_basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_basic())

    def rails_planted_bug(self, *, envelope: openapi_rails.Envelope) -> OpenAPIServer:
        return _start(self.parent, openapi_rails.planted_bug(envelope))

    def laravel_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_laravel.planted_bug())

    def stateful_users(self, *modifiers: Modifier[openapi_stateful.UserStore]) -> OpenAPIServer:
        return _start(self.parent, openapi_stateful.stateful_users(*modifiers))

    def aspnet_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.aspnet_planted_bug())

    def under_declared_security(
        self, *modifiers: Modifier[openapi_under_declared_security.UnderDeclaredSecurityStore]
    ) -> OpenAPIServer:
        return _start(self.parent, openapi_under_declared_security.under_declared_security(*modifiers))

    def zod_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_zod.planted_bug())

    def unimplemented_method(self) -> OpenAPIServer:
        return _start(self.parent, openapi_supervisor.unimplemented_method())

    def linked_with_unimplemented_method(self) -> OpenAPIServer:
        return _start(self.parent, openapi_supervisor.linked_with_unimplemented_method())


@dataclass(slots=True)
class GraphQLApps:
    parent: Context

    def books(self, *, endpoint: str = "/graphql", framework: Literal["flask", "fastapi"] = "flask") -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.books(endpoint=endpoint, framework=framework))

    def from_schema(
        self, schema, *, endpoint: str = "/graphql", framework: Literal["flask", "fastapi"] = "flask"
    ) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.from_schema(schema, endpoint=endpoint, framework=framework))

    def use_after_create(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.use_after_create())

    def generic_id_pool(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.generic_id_pool())

    def input_object_pool(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.input_object_pool())

    def list_argument_pool(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.list_argument_pool())

    def tombstone_pool(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.tombstone_pool())

    def use_after_delete(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.use_after_delete())

    def double_delete(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.double_delete())


@dataclass
class GraphQLContext:
    parent: Context

    @property
    def apps(self) -> GraphQLApps:
        return GraphQLApps(parent=self.parent)


@dataclass
class OpenApiContext:
    parent: Context

    @property
    def apps(self) -> OpenAPIApps:
        return OpenAPIApps(parent=self.parent)

    def build_schema(
        self, paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        return builders.build_schema(paths, version=version, **kwargs)

    def write_schema(
        self,
        paths: dict[str, Any],
        *,
        version: str = "3.0.2",
        format: str = "json",
        filename: str = "schema",
        **kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        schema = self.build_schema(paths, version=version, **kwargs)
        return self.parent.makefile(schema, format=format, filename=filename)

    def make_flask_app(
        self, paths: dict[str, Any], *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> tuple[Flask, dict[str, Any]]:
        return builders.make_flask_app(paths, version=version, **kwargs)

    def make_flask_app_from_schema(self, schema: dict[str, Any]) -> Flask:
        return builders.make_flask_app_from_schema(schema)


@dataclass
class Context:
    """Helper to localize common actions for testing API schemas."""

    request: pytest.FixtureRequest

    @property
    def openapi(self) -> OpenApiContext:
        return OpenApiContext(parent=self)

    @property
    def graphql(self) -> GraphQLContext:
        return GraphQLContext(parent=self)

    @property
    def _testdir(self):
        return self.request.getfixturevalue("testdir")

    def makefile(
        self, schema: dict[str, Any], *, format: str = "json", filename: str = "schema", parent: str | None = None
    ):
        if parent is not None:
            directory = self._testdir.mkdir(parent)
        else:
            directory = self._testdir.tmpdir
        if format == "json":
            path = directory / f"{filename}.json"
            path.write_text(json.dumps(schema), "utf8")
            return path
        if format == "yaml":
            path = directory / f"{filename}.yaml"
            path.write_text(yaml.dump(schema), "utf8")
            return path
        raise ValueError(f"Unknown format: {format}")

    def write_pymodule(self, content: str, *, filename: str = "module"):
        content = f"import schemathesis\nnote=print\n{dedent(content)}"
        module = self._testdir.makepyfile(**{filename: content})
        pkgroot = module.dirpath()
        module._ensuresyspath(True, pkgroot)
        return module.purebasename

    @contextmanager
    def check(self, content: str):
        with self.restore_checks():
            yield self.write_pymodule(content)

    @contextmanager
    def restore_checks(self):
        names = set(CHECKS.get_all_names())
        try:
            yield
        finally:
            new_names = set(CHECKS.get_all_names()) - names
            for name in new_names:
                CHECKS.unregister(name)

    @contextmanager
    def hook(self, content: str):
        with self.restore_hooks():
            yield self.write_pymodule(content)

    @contextmanager
    def restore_hooks(self):
        before = GLOBAL_HOOK_DISPATCHER.get_all()
        try:
            yield
        finally:
            GLOBAL_HOOK_DISPATCHER._hooks = before


@pytest.fixture
def restore_checks(ctx):
    with ctx.restore_checks():
        yield


@pytest.fixture
def ctx(request) -> Context:
    return Context(request=request)
