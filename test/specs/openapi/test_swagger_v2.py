from __future__ import annotations

import pytest

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.specs.openapi.checks import (
    _coerce_header_value,
    response_headers_conformance,
    response_schema_conformance,
)
from test.utils import EventStream


def test_baseline_e2e(ctx):
    api = ctx.openapi.apps.swagger_v2_baseline()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()

    api_calls = api.calls_under("/api/")
    assert api_calls
    assert {call.path for call in api_calls} == {"/api/baseline"}


def test_formdata_serialised_as_multipart(ctx):
    api = ctx.openapi.apps.swagger_v2_formdata()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    uploads = api.calls_to("/api/upload")
    assert uploads
    for call in uploads:
        assert call.headers.get("Content-Type", "").startswith("multipart/form-data")


def test_collection_format_serialization(ctx):
    api = ctx.openapi.apps.swagger_v2_collection_format()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=20, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_to("/api/search")
    assert captured

    delimiters = {"csv": ",", "ssv": " ", "tsv": "\t", "pipes": "|"}
    seen = dict.fromkeys(delimiters, False)
    for call in captured:
        for name, delimiter in delimiters.items():
            value = call.query.get(name)
            if value and delimiter in value:
                seen[name] = True
    missing = [name for name, hit in seen.items() if not hit]
    assert not missing, f"Did not observe delimited array for collectionFormat={missing}"


SECURITY_CASES = [
    pytest.param(
        "/private/api-key",
        {"headers": {"X-API-Key": "secret"}},
        ("X-Api-Key", "secret"),
        id="api-key",
    ),
    pytest.param(
        "/private/basic",
        {"auth": ("user", "pass")},
        ("Authorization", "Basic dXNlcjpwYXNz"),
        id="basic-auth",
    ),
]


@pytest.mark.parametrize(("path", "stream_kwargs", "expected_header"), SECURITY_CASES)
def test_security_credentials_propagate(ctx, path, stream_kwargs, expected_header):
    api = ctx.openapi.apps.swagger_v2_security()
    schema = schemathesis.openapi.from_url(api.schema_url).include(path=path)
    EventStream(schema, max_examples=3, **stream_kwargs).execute()

    captured = api.calls_to(f"/api{path}")
    header_name, expected_value = expected_header
    # Engine also drives auth-omitted negatives; require at least one positive carry.
    assert any(call.headers.get(header_name) == expected_value for call in captured)


def test_oauth2_definition_loads(ctx):
    api = ctx.openapi.apps.swagger_v2_oauth2_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    operations = list(schema.get_all_operations())
    assert all(not isinstance(op, Err) for op in operations)
    EventStream(schema, max_examples=2).execute()


def test_nullable_response_passes_validation(ctx):
    api = ctx.openapi.apps.swagger_v2_nullable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()


def test_examples_phase_uses_x_example(ctx):
    api = ctx.openapi.apps.swagger_v2_examples()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, phases=[PhaseName.EXAMPLES]).execute()

    examples_calls = api.calls_to("/api/examples")
    assert examples_calls
    bodies = [c.json() for c in examples_calls]
    assert {"name": "from-x-example"} in bodies


def test_response_header_validation_only_complains_about_headers(ctx):
    # The conformance check must, when it fails, fail with a header-specific message —
    # not surface generic body-schema errors against a header schema.
    api = ctx.openapi.apps.swagger_v2_response_headers()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=3).execute()

    assert api.calls_to("/api/headers")
    for finished in stream.find_all(events.ScenarioFinished):
        for checks in finished.recorder.checks.values():
            for check in checks:
                if check.status == Status.FAILURE:
                    failure = check.failure_info.failure if check.failure_info else None
                    assert failure is not None and "header" in str(failure).lower()


def test_default_response_used_when_status_undocumented(ctx):
    api = ctx.openapi.apps.swagger_v2_default_response()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()
    interactions = list(stream.find(events.ScenarioFinished, status=Status.SUCCESS).recorder.interactions.values())
    assert any(i.response.status_code == 418 for i in interactions)


PATH_PARAMETER_FACTORIES = [
    pytest.param("swagger_v2_array_path_parameter", "/api/items/", id="declared-array"),
    pytest.param("swagger_v2_injected_path_parameter", "/api/auto/", id="undeclared-injected"),
]


@pytest.mark.parametrize(("factory_name", "path_prefix"), PATH_PARAMETER_FACTORIES)
def test_path_parameter_reaches_endpoint(ctx, factory_name, path_prefix):
    api = getattr(ctx.openapi.apps, factory_name)()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()
    assert api.calls_under(path_prefix)


def test_all_parameter_locations_resolve(ctx):
    # Path + query + header + body ($ref'd) must all generate and reach the server in one shot.
    api = ctx.openapi.apps.swagger_v2_all_locations()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_under("/api/all/")
    assert captured
    for call in captured:
        assert "query_param" in call.query
        assert call.headers.get("X-Header-Param")


INVALID_SCHEMAS = [
    pytest.param(
        {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {
                    "get": {"parameters": {"name": "id", "in": "query"}, "responses": {"200": {"description": "OK"}}}
                }
            },
        },
        id="parameters-as-dict",
    ),
    pytest.param(
        {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/x": {"get": {"responses": "not-an-object"}}},
        },
        id="responses-as-string",
    ),
]


@pytest.mark.parametrize("raw", INVALID_SCHEMAS)
def test_invalid_schema_surfaces_invalid_schema_error(ctx, raw):
    schema = schemathesis.openapi.from_dict(raw)
    operations = list(schema.get_all_operations())
    assert operations
    assert all(isinstance(op, Err) for op in operations)
    assert isinstance(operations[0].err(), InvalidSchema)


def test_no_response_body_validation_passes(ctx):
    api = ctx.openapi.apps.swagger_v2_no_response_body()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_schema_conformance], max_examples=3).execute()
    stream.assert_no_failures()
    assert api.calls_to("/api/no-content")


def test_native_examples_phase_loads(ctx):
    api = ctx.openapi.apps.swagger_v2_native_response_examples()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, phases=[PhaseName.EXAMPLES, PhaseName.FUZZING], max_examples=3).execute()
    assert api.calls_to("/api/items")


def test_collection_format_multi_uses_repeated_query_keys(ctx):
    # `collectionFormat: multi` is not in `_serialize_swagger2` — the engine relies on
    # the HTTP client repeating the key (?multi=a&multi=b), not delimiting.
    api = ctx.openapi.apps.swagger_v2_collection_format()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=20, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_to("/api/search")
    assert any(r.raw_query.count("multi=") >= 2 for r in captured)


def test_optional_security_does_not_require_credentials(ctx):
    api = ctx.openapi.apps.swagger_v2_security()
    schema = schemathesis.openapi.from_url(api.schema_url).include(path="/private/optional")
    EventStream(schema, max_examples=3).execute()

    optional_calls = api.calls_to("/api/private/optional")
    # Engine reaches the endpoint even without credentials configured.
    assert any("Authorization" not in call.headers for call in optional_calls)


def test_parameter_ref_resolves(ctx):
    api = ctx.openapi.apps.swagger_v2_parameter_ref()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    listings = api.calls_to("/api/listing")
    assert listings
    assert all("page" in call.query for call in listings)


PATH_LEVEL_METHODS = [pytest.param("GET", id="get"), pytest.param("POST", id="post")]


@pytest.mark.parametrize("method", PATH_LEVEL_METHODS)
def test_path_level_parameters_apply_to_each_operation(ctx, method):
    # Path-item-level `parameters` are inherited by every operation under the path;
    # the engine must apply them to GET and POST alike.
    api = ctx.openapi.apps.swagger_v2_path_level_parameters()
    schema = schemathesis.openapi.from_url(api.schema_url).include(method=method)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_under("/api/path-shared/", method=method)
    assert captured
    assert all("trace" in call.query for call in captured)


def test_form_urlencoded_body_uses_urlencoding(ctx):
    api = ctx.openapi.apps.swagger_v2_form_urlencoded()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    posts = api.calls_to("/api/form-urlencoded")
    assert posts
    for call in posts:
        assert call.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded")


def test_multiple_path_parameters_resolve(ctx):
    api = ctx.openapi.apps.swagger_v2_multi_path_parameter()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, max_examples=3, modes=[schemathesis.GenerationMode.POSITIVE]).execute()

    captured = api.calls_under("/api/orgs/")
    assert captured
    for call in captured:
        # Path: /api/orgs/{org_id}/users/{user_id} — both segments must be filled.
        segments = call.path.split("/")
        assert len(segments) == 6
        assert segments[3] and segments[5]


def test_diverse_response_headers_accepted(ctx):
    # Integer / boolean / date-time string headers all validate cleanly.
    api = ctx.openapi.apps.swagger_v2_diverse_response_headers()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=3).execute()
    stream.assert_no_failures()


def test_array_response_header_with_collection_format(ctx):
    # Swagger 2.0 array response header serialised via collectionFormat: csv —
    # the validator must split on the delimiter before evaluating against `type: array`.
    api = ctx.openapi.apps.swagger_v2_array_response_header()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, checks=[response_headers_conformance], max_examples=3).execute()
    stream.assert_no_failures()


COLLECTION_FORMAT_HEADER_CASES = [
    pytest.param("csv", ",", id="csv"),
    pytest.param("ssv", " ", id="ssv"),
    pytest.param("tsv", "\t", id="tsv"),
    pytest.param("pipes", "|", id="pipes"),
]


@pytest.mark.parametrize(("collection_format", "delimiter"), COLLECTION_FORMAT_HEADER_CASES)
def test_array_response_header_collection_format_unit(collection_format, delimiter):
    # Each collectionFormat splits on the spec-defined delimiter and validates element-wise.
    schema = {"type": "array", "items": {"type": "integer"}, "collectionFormat": collection_format}
    coerced = _coerce_header_value(delimiter.join(["1", "2", "3"]), schema)
    assert coerced == [1, 2, 3]


def test_array_response_header_unknown_collection_format_passthrough():
    # `multi` (and any unrecognised format) is not splittable on a single header line; keep raw.
    schema = {"type": "array", "items": {"type": "string"}, "collectionFormat": "multi"}
    assert _coerce_header_value("a,b", schema) == "a,b"


def test_and_security_carries_both_credentials(ctx):
    # `security: [{api_key: [], basic_auth: []}]` requires both schemes on a single request.
    api = ctx.openapi.apps.swagger_v2_and_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema, headers={"X-API-Key": "k"}, auth=("u", "p"), max_examples=3).execute()

    captured = api.calls_to("/api/private/and")
    assert any(
        call.headers.get("X-Api-Key") == "k" and call.headers.get("Authorization") == "Basic dTpw" for call in captured
    )
