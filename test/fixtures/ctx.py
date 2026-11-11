from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Literal, overload

import pytest
import yaml
from flask import Flask

import schemathesis
from test.apps import builders
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import basic as openapi_basic
from test.apps.catalog.openapi import envelopes as openapi_envelopes
from test.apps.catalog.openapi import error_feedback as openapi_error_feedback
from test.apps.catalog.openapi import nested as openapi_nested
from test.apps.catalog.openapi import sign_up as openapi_sign_up
from test.apps.catalog.openapi import stateful as openapi_stateful
from test.apps.catalog.openapi import supervisor as openapi_supervisor
from test.apps.catalog.openapi import under_declared_security as openapi_under_declared_security
from test.apps.catalog.openapi import users as openapi_users
from test.apps.catalog.openapi import wfc as openapi_wfc
from test.apps.runtime import GraphQLApp, GraphQLServer, Modifier, OpenAPIApp, OpenAPIServer

STARTED_SERVERS = pytest.StashKey[list[OpenAPIServer]]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    servers = item.stash.get(STARTED_SERVERS, None)
    if report.failed and servers:
        text = "\n\n".join("\n".join([server.base_url, *map(repr, server.requests)]) for server in servers)
        report.sections.append(("Captured requests", text))
    return report


if TYPE_CHECKING:
    from collections.abc import Callable

    from schemathesis.config import SchemathesisConfig
    from schemathesis.openapi.loaders import OpenApiSchema
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.graphql.schemas import GraphQLSchema
    from test.apps.catalog.grpc.raw import RawGrpcServer
    from test.apps.catalog.grpc.users import CrashingGrpcServer, GrpcApp, GrpcServer


@overload
def _start(parent: Context, app: OpenAPIApp) -> OpenAPIServer: ...


@overload
def _start(parent: Context, app: GraphQLApp) -> GraphQLServer: ...


def _start(parent: Context, app: OpenAPIApp | GraphQLApp) -> OpenAPIServer | GraphQLServer:
    app_runner = parent.request.getfixturevalue("app_runner")
    runner = app_runner.run_flask_app if app.kind == "flask" else app_runner.run_asgi_app
    server = app.make_server(port=runner(app.server))
    if isinstance(server, OpenAPIServer):
        parent.request.node.stash.setdefault(STARTED_SERVERS, []).append(server)
    return server


@dataclass(slots=True)
class OpenAPIApps:
    parent: Context

    def success(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success())

    def failure(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.failure())

    def multiple_failures(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.multiple_failures())

    def crash_closes_connection(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.crash_closes_connection())

    def custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.custom_format())

    def identical_query_parameters(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.identical_query_parameters())

    def flaky(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.flaky())

    def multipart(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.multipart())

    def csv_payload(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.csv_payload())

    def ignored_auth(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.ignored_auth())

    def upload_file(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.upload_file())

    def always_incorrect(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.always_incorrect())

    def missing_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.missing_path_parameter())

    def text(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.text())

    def plain_text_body(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.plain_text_body())

    def teapot(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.teapot())

    def malformed_json(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.malformed_json())

    def invalid(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.invalid())

    def success_and_text(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_text())

    def slow(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.slow())

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

    def success_and_failure(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_failure())

    def path_variable_and_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.path_variable_and_custom_format())

    def basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.basic())

    def success_and_basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_basic())

    def schema_unavailable(self, *, failures: int = 1) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.schema_unavailable(failures))

    def rails_planted_bug(self, *, envelope: openapi_envelopes.RailsEnvelope) -> OpenAPIServer:
        return _start(self.parent, openapi_envelopes.planted_bug(openapi_envelopes.RAILS[envelope]))

    def stateful_users(self, *modifiers: Modifier[openapi_stateful.UserStore]) -> OpenAPIServer:
        return _start(self.parent, openapi_stateful.stateful_users(*modifiers))

    def flaky_branch_blocks(self) -> OpenAPIServer:
        return _start(self.parent, openapi_stateful.flaky_branch_blocks())

    def users_crud(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.crud())

    def users_crud_with_success(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.crud_with_success())

    def linked_users(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.linked_users())

    def under_declared_security(
        self, *modifiers: Modifier[openapi_under_declared_security.UnderDeclaredSecurityStore]
    ) -> OpenAPIServer:
        return _start(self.parent, openapi_under_declared_security.under_declared_security(*modifiers))

    def sign_up_and_login(self, behavior: openapi_sign_up.SignUpBehavior = "ok") -> OpenAPIServer:
        return _start(self.parent, openapi_sign_up.sign_up_and_login(behavior))

    def unimplemented_method(self) -> OpenAPIServer:
        return _start(self.parent, openapi_supervisor.unimplemented_method())

    def wfc_login(self) -> OpenAPIServer:
        return _start(self.parent, openapi_wfc.wfc_login())

    def wfc_login_plain(self) -> OpenAPIServer:
        return _start(self.parent, openapi_wfc.wfc_login_plain())

    def wfc_role_gated(self, deny_status: int = 403) -> OpenAPIServer:
        return _start(self.parent, openapi_wfc.wfc_role_gated(deny_status=deny_status))

    def wfc_owned_orders(
        self, policy: str, listing: str | None = None, writes: bool = False, security: str = "declared"
    ) -> OpenAPIServer:
        return _start(self.parent, openapi_wfc.wfc_owned_orders(policy, listing, writes, security))

    def planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.planted_bug())

    def deep_leaf_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.deep_leaf_bug())

    def header_constraint_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.header_constraint_bug())


@dataclass(slots=True)
class GraphQLApps:
    parent: Context

    def books(self, *, endpoint: str = "/graphql", framework: Literal["flask", "fastapi"] = "flask") -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.books(endpoint=endpoint, framework=framework))

    def federated_subgraph(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.federated_subgraph())

    def from_schema(self, schema, *, framework: Literal["flask", "fastapi"] = "flask") -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.from_schema(schema, framework=framework))

    def canned_response(self, payload: object, *, status_code: int = 200) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.canned_response(payload, status_code=status_code))

    def use_after_create(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.use_after_create())

    def generic_id_pool(self, *, fixed: bool = False) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.generic_id_pool(fixed=fixed))


@dataclass
class GraphQLContext:
    parent: Context

    @property
    def apps(self) -> GraphQLApps:
        return GraphQLApps(parent=self.parent)

    def serve(self, app: GraphQLApp) -> GraphQLServer:
        return _start(self.parent, app)

    def load_sdl(self, sdl: str, *, config: SchemathesisConfig | None = None) -> GraphQLSchema:
        return schemathesis.graphql.from_file(sdl, config=config)

    def write_sdl(self, sdl: str, *, filename: str = "schema.graphql") -> Path:
        path = self.parent.request.getfixturevalue("tmp_path") / filename
        path.write_text(sdl)
        return path

    def load_introspection(
        self, raw_schema: dict[str, Any], *, config: SchemathesisConfig | None = None
    ) -> GraphQLSchema:
        return schemathesis.graphql.from_dict(raw_schema, config=config)


@dataclass(slots=True)
class GrpcApps:
    parent: Context

    def users(
        self,
        *,
        tls: bool = False,
        client_auth: bool = False,
        reflection: str | None = "v1alpha",
        reflection_services: tuple[str, ...] = ("pkg.Users", "pkg.Admin", "grpc.health.v1.Health"),
        undescribed_services: tuple[str, ...] = (),
        reflection_imports: bool = True,
        unserved_files: tuple[str, ...] = (),
        emptied_files: tuple[str, ...] = (),
        annotated: bool = False,
        start: bool = True,
        port: int = 0,
        on_get_user: Callable[[int, Any], object] | None = None,
    ) -> GrpcServer:
        # grpcio has no free-threaded build, so only tests that start a server need it.
        from test.apps.catalog.grpc import users as grpc_users

        app = grpc_users.users(
            tls=tls,
            client_auth=client_auth,
            reflection_version=reflection,
            reflection_services=reflection_services,
            undescribed_services=undescribed_services,
            reflection_imports=reflection_imports,
            unserved_files=unserved_files,
            emptied_files=emptied_files,
            annotated=annotated,
            start=start,
            port=port,
            on_get_user=on_get_user,
        )
        return self._serve(app)

    def crashing_users(self, how: str) -> CrashingGrpcServer:
        """`users` where a call with id 500 brings the server down: `exit` drops connections, `stop` cancels calls."""
        from test.apps.catalog.grpc.users import crashing_users

        server = crashing_users(how, self.parent.request.getfixturevalue("tmp_path"))
        self.parent.request.addfinalizer(server.kill)
        return server

    def side_effects(self) -> GrpcServer:
        from test.apps.catalog.grpc.users import side_effects

        return self._serve(side_effects())

    def library(self, *, fixed: bool = False) -> GrpcServer:
        from test.apps.catalog.grpc.users import library

        return self._serve(library(fixed=fixed))

    def envelope(self) -> GrpcServer:
        from test.apps.catalog.grpc.users import envelope

        return self._serve(envelope())

    def echo_status(self) -> RawGrpcServer:
        from test.apps.catalog.grpc.raw import echo_status

        server = echo_status(self.parent.request.getfixturevalue("tmp_path"))
        self.parent.request.addfinalizer(server.stop)
        return server

    def _serve(self, app: GrpcApp) -> GrpcServer:
        server = app.make_server(self.parent.request.getfixturevalue("tmp_path"))
        self.parent.request.addfinalizer(server.close)
        return server


@dataclass(slots=True)
class GrpcContext:
    parent: Context

    @property
    def apps(self) -> GrpcApps:
        pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
        return GrpcApps(parent=self.parent)


@dataclass
class OpenApiContext:
    parent: Context

    @property
    def apps(self) -> OpenAPIApps:
        return OpenAPIApps(parent=self.parent)

    def serve(self, app: OpenAPIApp) -> OpenAPIServer:
        return _start(self.parent, app)

    def build_schema(
        self, paths: dict[str, Any] | None, *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        return builders.build_schema(paths, version=version, **kwargs)

    def load_schema(
        self,
        paths: dict[str, Any] | None,
        *,
        version: str = "3.0.2",
        config: SchemathesisConfig | None = None,
        **kwargs: dict[str, Any],
    ) -> OpenApiSchema:
        schema = self.build_schema(paths, version=version, **kwargs)
        return schemathesis.openapi.from_dict(schema, config=config)

    def load_operation(
        self,
        label: str,
        definition: dict[str, Any],
        *,
        version: str = "3.0.2",
        config: SchemathesisConfig | None = None,
        **kwargs: dict[str, Any],
    ) -> APIOperation:
        # `label` is "METHOD /path", e.g. "POST /users".
        method, path = label.split(" ", 1)
        schema = self.load_schema({path: {method.lower(): definition}}, version=version, config=config, **kwargs)
        return schema[path][method]

    def from_full_schema(self, raw_schema: dict[str, Any]) -> OpenApiSchema:
        version = raw_schema.get("openapi") or raw_schema["swagger"]
        kwargs = {key: value for key, value in raw_schema.items() if key not in ("openapi", "swagger", "paths")}
        return self.load_schema(raw_schema["paths"], version=version, **kwargs)

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

    def make_permissive_flask_app(self, schema: dict[str, Any]) -> Flask:
        return builders.make_permissive_flask_app(schema)


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
    def grpc(self) -> GrpcContext:
        return GrpcContext(parent=self)

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


@pytest.fixture
def ctx(request) -> Context:
    return Context(request=request)
