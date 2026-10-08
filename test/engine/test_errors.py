import re

import hypothesis.errors
import jsonschema_rs
import pytest
import requests
from flask import jsonify, request
from hypothesis import strategies as st

import schemathesis
from schemathesis.config import ConfigError
from schemathesis.core.errors import (
    AuthenticationError,
    InternalError,
    InvalidRegexPattern,
    InvalidRegexType,
    InvalidSchema,
    UnboundPrefix,
)
from schemathesis.engine import Status, events
from schemathesis.engine.errors import EngineErrorInfo, deduplicate_errors
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from test.utils import EventStream


def test_config_error_has_no_useful_traceback():
    info = EngineErrorInfo(ConfigError("boom"))
    assert info.has_useful_traceback is False
    assert info.title == "Configuration Error"


def test_authentication_error_traceback_visibility():
    assert EngineErrorInfo(AuthenticationError("P", "get", "boom")).has_useful_traceback is False
    assert EngineErrorInfo(AuthenticationError("P", "get", "boom", show_traceback=True)).has_useful_traceback is True


def test_deduplicate_errors():
    errors = [
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%5Dw2y%C3%9D (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23db4ce0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%C3%8BEK (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23e2a6c0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
    ]
    assert len(list(deduplicate_errors(errors))) == 1


def _fuzz_errors(schema) -> tuple[list[events.NonFatalError], list[Status]]:
    stream = EventStream(schema, phases=[PhaseName.FUZZING], max_examples=10, modes=[GenerationMode.POSITIVE]).execute()
    return (
        stream.find_all(events.NonFatalError),
        [event.status for event in stream.find_all(events.ScenarioFinished)],
    )


def _schema_with_query(ctx, query_schema):
    return ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": query_schema}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )


def _fail_with(exception_factory):
    def before_generate_query(context, strategy):
        def fail(value):
            raise exception_factory(value)

        return strategy.map(fail)

    return before_generate_query


def test_filter_hook_rejecting_every_case_reports_failed_health_check(ctx):
    schema = _schema_with_query(ctx, {"type": "string"})

    @schema.hook
    def filter_query(context, query):
        return False

    errors, statuses = _fuzz_errors(schema)
    assert [event.info.format() for event in errors] == [
        "Failed Health Check\n\n"
        "Too many generated examples are filtered out for this operation\n\n"
        "Unable to identify the specific parameter. Common causes:\n"
        "  - Complex regex patterns that match few strings\n"
        "  - Multiple overlapping constraints (pattern + format + enum)\n\n"
        "Tip: Simplify constraints or widen acceptable value ranges or bypass this health check using "
        "`--suppress-health-check=filter_too_much`."
    ]
    assert statuses == [Status.ERROR]


def test_custom_format_with_huge_minimum_reports_slow_parameter(ctx):
    schemathesis.openapi.format("huge", st.text(min_size=50000, max_size=50000))
    schema = _schema_with_query(ctx, {"type": "string", "format": "huge"})
    errors, statuses = _fuzz_errors(schema)
    assert [event.info.format() for event in errors] == [
        "Failed Health Check\n\n"
        "Minimum possible example is too large for query parameter 'q'\n"
        "Schema:\n\n"
        '{\n    "type": "string",\n    "format": "huge"\n}\n\n'
        "This usually means:\n"
        "  - Arrays with large minimum size (e.g., minItems: 100)\n"
        "  - Many required properties with their own large minimums\n"
        "  - Nested structures that multiply size requirements\n\n"
        "Tip: Reduce minimum size requirements or number of required properties or bypass this health check using "
        "`--suppress-health-check=large_base_example`."
    ]
    assert statuses == [Status.ERROR]


def test_custom_format_with_invalid_bounds_reports_invalid_argument(ctx):
    schemathesis.openapi.format("inverted", st.integers(min_value=5, max_value=1).map(str))
    schema = _schema_with_query(ctx, {"type": "string", "format": "inverted"})
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [
        (hypothesis.errors.InvalidArgument, "Cannot have max_value=1 < min_value=5")
    ]
    assert statuses == [Status.ERROR]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("boom", "Unexpected error during testing of this API operation: boom"),
        ("", "Unexpected error during testing of this API operation"),
    ],
    ids=["with-message", "without-message"],
)
def test_assertion_error_in_hook_reports_internal_error(ctx, message, expected):
    schema = _schema_with_query(ctx, {"type": "string"})
    schema.hook("before_generate_query")(_fail_with(lambda value: AssertionError(message)))
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [(InternalError, expected)]
    assert statuses == [Status.ERROR]


def test_assertion_error_in_hook_reports_schema_error_for_invalid_schema(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": 5}},
                }
            }
        }
    )
    schema.hook("before_generate_query")(_fail_with(lambda value: AssertionError("boom")))
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [
        (
            InvalidSchema,
            "Invalid definition for element at index 200 in `responses`\n\n"
            "Location:\n    paths -> /items -> get -> responses -> 200\n\n"
            'Problematic definition:\n    {\n        "description": 5\n    }\n\n'
            "Error details:\n    The provided definition doesn't match any of the expected formats or types.\n\n"
            "Ensure that the definition complies with the OpenAPI specification",
        )
    ]
    assert statuses == [Status.ERROR]


def test_generation_changing_between_replays_is_not_reported_as_error(ctx, app_runner):
    # Values learned from responses change what a replayed test case draws, like this filter does.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "n", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({}), 200 if request.args.get("n", type=int, default=0) % 2 == 0 else 400

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    calls = []

    def rejects_first_value(value):
        calls.append(value)
        return len(calls) > 1

    @schema.hook
    def before_generate_case(context, strategy):
        return st.tuples(st.booleans().filter(rejects_first_value), strategy).map(lambda drawn: drawn[1])

    stream = EventStream(
        schema, phases=[PhaseName.FUZZING], max_examples=20, seed=1, modes=[GenerationMode.POSITIVE]
    ).execute()
    assert [(type(event.value), str(event.value)) for event in stream.find_all(events.NonFatalError)] == []
    assert [event.status for event in stream.find_all(events.ScenarioFinished)] == [Status.SUCCESS]


def test_non_string_pattern_in_hook_reports_invalid_regex_type(ctx):
    schema = _schema_with_query(ctx, {"type": "string"})
    schema.hook("before_generate_query")(_fail_with(lambda value: _compile_pattern(0.0)))
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [
        (
            InvalidRegexType,
            "Invalid `pattern` value: expected a string. If your schema is in YAML, ensure `pattern` values are quoted",
        )
    ]
    assert statuses == [Status.ERROR]


def _invalid_regex_error():
    try:
        jsonschema_rs.validator_for({"format": "regex"}, validate_formats=True).validate("[a-")
    except jsonschema_rs.ValidationError as exc:
        return exc


def _type_mismatch_error():
    try:
        jsonschema_rs.validate({"type": "integer"}, "x")
    except jsonschema_rs.ValidationError as exc:
        return exc


def _raise_in_map_case(schema, error_for):
    def map_case(context, case):
        raise error_for(case.query["q"])

    schema.hook("map_case")(map_case)


def test_invalid_regex_in_hook_reports_schema_error(ctx):
    schema = _schema_with_query(ctx, {"type": "string"})
    _raise_in_map_case(schema, lambda value: _invalid_regex_error())
    errors, statuses = _fuzz_errors(schema)
    assert [event.info.format() for event in errors] == [
        "Schema Error\n\n"
        "Failed to generate test cases for this API operation because of unsupported regular expression `[a-`\n\n"
        "Tip: Ensure your regex follows ECMA 262 (JavaScript) syntax.\n"
        "For guidance, visit: https://json-schema.org/understanding-json-schema/reference/regular_expressions"
    ]
    assert statuses == [Status.ERROR]


def test_validation_error_in_hook_is_reported_as_is(ctx):
    schema = _schema_with_query(ctx, {"type": "string"})
    _raise_in_map_case(schema, lambda value: _type_mismatch_error())
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [
        (
            jsonschema_rs.ValidationError,
            '"x" is not of type "integer"\n\nFailed validating "type" in schema\n\nOn instance:\n    "x"',
        )
    ]
    assert statuses == [Status.ERROR]


# Errors raised alongside an invalid regex usually restate it, so only the regex error is shown.
def test_invalid_regex_hides_other_hook_errors(ctx):
    schema = _schema_with_query(ctx, {"type": "string"})
    _raise_in_map_case(schema, lambda value: _invalid_regex_error() if value else ValueError("empty"))
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), str(event.value)) for event in errors] == [
        (
            InvalidRegexPattern,
            "Failed to generate test cases for this API operation because of unsupported regular expression `[a-`",
        )
    ]
    assert statuses == [Status.ERROR]


def test_custom_format_over_empty_elements_reports_unsatisfiable(ctx):
    schemathesis.openapi.format("no-elements", st.lists(st.nothing().map(str), max_size=3).map(str))
    schema = _schema_with_query(ctx, {"type": "string", "format": "no-elements"})
    errors, statuses = _fuzz_errors(schema)
    assert [event.info.format() for event in errors] == [
        "Schema Error\n\n"
        "Cannot generate test data for query parameter 'q'\n"
        "Schema:\n\n"
        '{\n    "type": "string",\n    "format": "no-elements"\n}\n\n'
        "This usually means:\n"
        "  - Type mismatch (e.g., enum with strings but type: integer)\n"
        "  - Contradictory constraints (e.g., minimum > maximum)\n"
        "  - Regex that's too complex to generate values for\n\n"
        "Tip: Review all parameters and request body schemas for conflicting constraints."
    ]
    assert statuses == [Status.ERROR]


def test_custom_format_beyond_size_limit_in_examples_reports_failed_health_check(ctx):
    schemathesis.openapi.format("oversized", st.lists(st.just("a"), min_size=10**7).map("".join))
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "format": "oversized"},
                        }
                    ],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}, "example": {"x": 1}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    stream = EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()
    assert [event.info.format() for event in stream.find_all(events.NonFatalError)] == [
        "Failed Health Check\n\n"
        "ListStrategy(just('a'), min_size=10_000_000, max_size=inf) can never generate a value, because min_size is "
        "larger than Hypothesis supports.  Including it is at best slowing down your tests for no benefit; at worst "
        "making them fail (maybe flakily) with a HealthCheck error.\n\n"
        "Tip: Reduce minimum size requirements or number of required properties or bypass this health check using "
        "`--suppress-health-check=large_base_example`."
    ]
    assert [event.status for event in stream.find_all(events.ScenarioFinished)] == [Status.ERROR]


def _compile_pattern(pattern):
    try:
        re.compile(pattern)
    except TypeError as exc:
        return exc


def test_tls_handshake_with_plain_http_server_reports_ssl_error(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.update(base_url=api.base_url.replace("http://", "https://"))
    errors, statuses = _fuzz_errors(schema)
    assert {type(event.value) for event in errors} == {requests.exceptions.SSLError}
    assert errors[0].info.format().startswith("Network Error\n\nSSL verification problem\n\n")
    assert errors[0].info.format().endswith("\n\nTip: Bypass SSL verification with `--tls-verify=false`.")
    assert statuses == [Status.ERROR]


def test_xml_prefix_without_namespace_reports_serialization_error(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/xml": {
                                "schema": {
                                    "type": "object",
                                    "xml": {"name": "item"},
                                    "required": ["a"],
                                    "properties": {"a": {"type": "string", "xml": {"prefix": "x"}}},
                                },
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    errors, statuses = _fuzz_errors(schema)
    assert [(type(event.value), event.info.title) for event in errors] == [(UnboundPrefix, "XML serialization error")]
    assert str(errors[0].value).startswith("Unbound prefix: `x`. You need to define this namespace")
    assert statuses == [Status.ERROR]
