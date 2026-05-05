from __future__ import annotations

import schemathesis
from schemathesis.core.errors import (
    AuthenticationError,
    InfiniteRecursiveReference,
    InvalidHeadersExample,
    InvalidRegexPattern,
    UnresolvableReference,
)
from schemathesis.core.jsonschema import bundle_for_generation, make_validator_for
from schemathesis.core.jsonschema.resolver import make_root_resolver
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.specs.openapi.examples import extract_from_schema
from test.utils import EventStream


def _examples_only(schema) -> EventStream:
    return EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()


def _schema_with_query_and_body_example(ctx, *, query_schema=None, extra_operation=None):
    operation = {
        "parameters": [
            {
                "name": "key",
                "in": "query",
                "required": True,
                "schema": query_schema or {"type": "string"},
            }
        ],
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                    "example": {"x": "any"},
                }
            }
        },
        "responses": {"200": {"description": "OK"}},
    }
    if extra_operation is not None:
        operation.update(extra_operation)
    return ctx.openapi.build_schema({"/items": {"post": operation}})


def test_create_test_auth_provider_failure_surfaces_as_non_fatal_error(ctx):
    # Auth provider raising during example strategy materialization must reach the user as a
    # NonFatalError instead of crashing the worker.
    @schemathesis.auth()
    class _BrokenAuth:
        def get(self, case, context):
            raise AuthenticationError("provider", "get", "creds expired")

        def set(self, case, data, context):
            pass

    schema_dict = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "security": [{"bearer": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                                "example": {"x": "any"},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    )
    loaded = schemathesis.openapi.from_dict(schema_dict)
    try:
        stream = _examples_only(loaded)
    finally:
        schemathesis.auths.unregister()

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, AuthenticationError) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_invalid_headers_example_mark(ctx):
    # Header example containing a newline character is rejected at the wire level.
    schema = ctx.openapi.build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "X-Custom",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "bad\nvalue",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    loaded = schemathesis.openapi.from_dict(schema)
    stream = _examples_only(loaded)
    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, InvalidHeadersExample) for event in errors), [
        type(e.value).__name__ for e in errors
    ]


def test_invalid_regex_example_generation_error(ctx):
    schema = _schema_with_query_and_body_example(
        ctx,
        query_schema={"type": "string", "pattern": "[invalid"},
    )
    loaded = schemathesis.openapi.from_dict(schema)

    @loaded.hook
    def before_generate_query(context, strategy):
        invalid_schema = context.operation.query[0].definition["schema"]
        make_validator_for(invalid_schema)
        return strategy

    stream = _examples_only(loaded)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, InvalidRegexPattern) for event in errors), [
        type(e.value).__name__ for e in errors
    ]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_infinite_recursive_reference_example_generation_error(ctx):
    recursive_schema = {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "required": ["child"],
                    "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                }
            }
        },
        "$ref": "#/components/schemas/Node",
    }
    schema = _schema_with_query_and_body_example(
        ctx,
        extra_operation={"x-recursive-schema": recursive_schema},
    )
    loaded = schemathesis.openapi.from_dict(schema)

    @loaded.hook
    def before_generate_query(context, strategy):
        schema = context.operation.definition.raw["x-recursive-schema"]

        def bundle(value):
            bundle_for_generation(schema, make_root_resolver(schema))
            return value

        return strategy.map(bundle)

    stream = _examples_only(loaded)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, InfiniteRecursiveReference) for event in errors), [
        type(e.value).__name__ for e in errors
    ]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_unresolvable_reference_example_generation_error(ctx):
    schema = _schema_with_query_and_body_example(
        ctx,
        extra_operation={"x-unresolvable-schema": {"$ref": "#/components/schemas/Missing"}},
    )
    loaded = schemathesis.openapi.from_dict(schema)

    @loaded.hook
    def before_generate_query(context, strategy):
        schema = context.operation.definition.raw["x-unresolvable-schema"]

        def extract(value):
            list(
                extract_from_schema(
                    operation=context.operation,
                    schema=schema,
                    example_keyword="example",
                    examples_container_keyword="examples",
                    resolver=make_root_resolver(schema),
                    reference_path=(),
                    bundle_storage=None,
                    merge_ref_siblings=context.operation.schema.adapter.ref_siblings,
                )
            )
            return value

        return strategy.map(extract)

    stream = _examples_only(loaded)

    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any(isinstance(event.value, UnresolvableReference) for event in errors), [
        type(e.value).__name__ for e in errors
    ]
    finished = stream.find_all(events.ScenarioFinished, phase=PhaseName.EXAMPLES)
    assert any(event.status == Status.ERROR for event in finished), [event.status for event in finished]


def test_missing_path_parameters_mark(ctx):
    api = ctx.openapi.apps.missing_path_parameter()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = _examples_only(schema)
    errors = stream.find_all(events.NonFatalError, phase=PhaseName.EXAMPLES)
    assert any("Path parameter 'id' is not defined" in str(event.value) for event in errors), [
        str(e.value) for e in errors
    ]
