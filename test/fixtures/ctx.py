from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Literal, overload

import pytest
import yaml
from flask import Flask

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER
from test.apps import builders
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import ajv as openapi_ajv
from test.apps.catalog.openapi import basic as openapi_basic
from test.apps.catalog.openapi import confluent as openapi_confluent
from test.apps.catalog.openapi import error_feedback as openapi_error_feedback
from test.apps.catalog.openapi import flask_rest as openapi_flask_rest
from test.apps.catalog.openapi import go_validator as openapi_go_validator
from test.apps.catalog.openapi import laravel as openapi_laravel
from test.apps.catalog.openapi import litestar as openapi_litestar
from test.apps.catalog.openapi import marshmallow as openapi_marshmallow
from test.apps.catalog.openapi import nested as openapi_nested
from test.apps.catalog.openapi import rails as openapi_rails
from test.apps.catalog.openapi import stateful as openapi_stateful
from test.apps.catalog.openapi import supervisor as openapi_supervisor
from test.apps.catalog.openapi import swagger_v2 as openapi_swagger_v2
from test.apps.catalog.openapi import symfony as openapi_symfony
from test.apps.catalog.openapi import under_declared_security as openapi_under_declared_security
from test.apps.catalog.openapi import users as openapi_users
from test.apps.catalog.openapi import zod as openapi_zod
from test.apps.runtime import GraphQLApp, GraphQLServer, Modifier, OpenAPIApp, OpenAPIServer

if TYPE_CHECKING:
    from schemathesis.config import SchemathesisConfig
    from schemathesis.openapi.loaders import OpenApiSchema
    from schemathesis.specs.graphql.schemas import GraphQLSchema


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

    def form(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.form())

    def upload_file(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.upload_file())

    def always_incorrect(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.always_incorrect())

    def success_and_upload_file(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_upload_file())

    def upload_file_and_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.upload_file_and_custom_format())

    def success_and_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_custom_format())

    def success_failure_unsatisfiable_empty_string(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_failure_unsatisfiable_empty_string())

    def empty(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.empty())

    def no_operations(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.no_operations())

    def empty_string(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.empty_string())

    def recursive(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.recursive())

    def invalid_response(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.invalid_response())

    def invalid_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.invalid_path_parameter())

    def missing_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.missing_path_parameter())

    def reserved(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.reserved())

    def conformance(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.conformance())

    def cp866(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.cp866())

    def read_only(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.read_only())

    def write_only(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.write_only())

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

    def chunked_success(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.chunked_success())

    def success_text_and_write_only(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_text_and_write_only())

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

    def success_and_failure(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_failure())

    def success_failure_multiple_failures_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_failure_multiple_failures_custom_format())

    def path_variable_and_custom_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.path_variable_and_custom_format())

    def basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.basic())

    def success_and_basic(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.success_and_basic())

    def sessions_and_log_event(self) -> OpenAPIServer:
        return _start(self.parent, openapi_basic.sessions_and_log_event())

    def rails_planted_bug(self, *, envelope: openapi_rails.Envelope) -> OpenAPIServer:
        return _start(self.parent, openapi_rails.planted_bug(envelope))

    def laravel_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_laravel.planted_bug())

    def stateful_users(self, *modifiers: Modifier[openapi_stateful.UserStore]) -> OpenAPIServer:
        return _start(self.parent, openapi_stateful.stateful_users(*modifiers))

    def users_crud(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.crud())

    def users_create_only(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.create_user_only())

    def users_crud_with_success(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.crud_with_success())

    def users_crud_with_failure(self) -> OpenAPIServer:
        return _start(self.parent, openapi_users.crud_with_failure())

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

    def planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.planted_bug())

    def nested_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.nested_planted_bug())

    def size_bound_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.size_bound_planted_bug())

    def format_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.format_planted_bug())

    def numeric_bound_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.numeric_bound_planted_bug())

    def positive_numeric_gate_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.positive_numeric_gate_planted_bug())

    def unrecognized_property_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.unrecognized_property_planted_bug())

    def pattern_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.pattern_planted_bug())

    def jackson_typed_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.jackson_typed_planted_bug())

    def jackson_typed_planted_bug_ref_bundled(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.jackson_typed_planted_bug_ref_bundled())

    def jackson_overflow_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.jackson_overflow_planted_bug())

    def jackson_enum_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.jackson_enum_planted_bug())

    def missing_query_param_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.missing_query_param_planted_bug())

    def missing_request_body_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.missing_request_body_planted_bug())

    def pydantic_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.pydantic_planted_bug())

    def commit_date_with_example(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.commit_date_with_example())

    def commit_date_with_examples(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.commit_date_with_examples())

    def commit_date_with_link(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.commit_date_with_link())

    def token_with_example(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.token_with_example())

    def token_with_examples(self) -> OpenAPIServer:
        return _start(self.parent, openapi_error_feedback.token_with_examples())

    def ajv_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_ajv.planted_bug())

    def go_validator_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_go_validator.planted_bug())

    def symfony_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_symfony.planted_bug())

    def marshmallow_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_marshmallow.planted_bug())

    def confluent_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_confluent.planted_bug())

    def flask_rest_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_flask_rest.planted_bug())

    def litestar_planted_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_litestar.planted_bug())

    def swagger_v2_baseline(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.baseline())

    def swagger_v2_formdata(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.formdata())

    def swagger_v2_collection_format(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.collection_format())

    def swagger_v2_security(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.security())

    def swagger_v2_nullable(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.nullable())

    def swagger_v2_examples(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.examples())

    def swagger_v2_response_headers(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.response_headers())

    def swagger_v2_default_response(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.default_response())

    def swagger_v2_array_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.array_path_parameter())

    def swagger_v2_injected_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.injected_path_parameter())

    def swagger_v2_all_locations(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.all_locations())

    def swagger_v2_oauth2_security(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.oauth2_security())

    def swagger_v2_no_response_body(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.no_response_body())

    def swagger_v2_native_response_examples(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.native_response_examples())

    def swagger_v2_parameter_ref(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.parameter_ref())

    def swagger_v2_path_level_parameters(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.path_level_parameters())

    def swagger_v2_form_urlencoded(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.form_urlencoded())

    def swagger_v2_multi_path_parameter(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.multi_path_parameter())

    def swagger_v2_diverse_response_headers(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.diverse_response_headers())

    def swagger_v2_array_response_header(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.array_response_header())

    def swagger_v2_and_security(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.and_security())

    def swagger_v2_kitchen_sink(self) -> OpenAPIServer:
        return _start(self.parent, openapi_swagger_v2.kitchen_sink())

    def deep_leaf_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.deep_leaf_bug())

    def header_constraint_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.header_constraint_bug())

    def query_array_items_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.query_array_items_bug())

    def one_of_branch_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.one_of_branch_bug())

    def additional_properties_bug(self) -> OpenAPIServer:
        return _start(self.parent, openapi_nested.additional_properties_bug())


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

    def slow_mutation(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.slow_mutation())

    def double_delete(self) -> GraphQLServer:
        return _start(self.parent, graphql_bookstore.double_delete())


@dataclass
class GraphQLContext:
    parent: Context

    @property
    def apps(self) -> GraphQLApps:
        return GraphQLApps(parent=self.parent)

    def load_sdl(self, sdl: str, *, config: SchemathesisConfig | None = None) -> GraphQLSchema:
        return schemathesis.graphql.from_file(sdl, config=config)

    def load_introspection(
        self, raw_schema: dict[str, Any], *, config: SchemathesisConfig | None = None
    ) -> GraphQLSchema:
        return schemathesis.graphql.from_dict(raw_schema, config=config)


@dataclass
class OpenApiContext:
    parent: Context

    @property
    def apps(self) -> OpenAPIApps:
        return OpenAPIApps(parent=self.parent)

    def build_schema(
        self, paths: dict[str, Any] | None, *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        return builders.build_schema(paths, version=version, **kwargs)

    def load_schema(
        self, paths: dict[str, Any] | None, *, version: str = "3.0.2", **kwargs: dict[str, Any]
    ) -> OpenApiSchema:
        schema = self.build_schema(paths, version=version, **kwargs)
        return schemathesis.openapi.from_dict(schema)

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
