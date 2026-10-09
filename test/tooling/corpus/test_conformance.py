from __future__ import annotations

import dataclasses

import pytest

from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)
from tools.corpus.conformance import ConformanceViolation, check_conformance, evaluate_conformance

SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
VALID_BODY = {"name": "alice"}
INVALID_BODY = {"name": 42}
BROKEN_SCHEMA = {"type": "object", "properties": {"x": {"$ref": "#/missing"}}}


@pytest.mark.parametrize(
    ("value", "schema", "is_negative", "expected"),
    [
        (VALID_BODY, SCHEMA, False, None),
        (
            INVALID_BODY,
            SCHEMA,
            False,
            ConformanceViolation(ParameterLocation.BODY, "application/json", INVALID_BODY, expected_valid=True),
        ),
        (INVALID_BODY, SCHEMA, True, None),
        (
            VALID_BODY,
            SCHEMA,
            True,
            ConformanceViolation(ParameterLocation.BODY, "application/json", VALID_BODY, expected_valid=False),
        ),
        ({"x": 1}, BROKEN_SCHEMA, False, None),
    ],
    ids=[
        "positive-valid",
        "positive-invalid",
        "negative-invalid",
        "negative-valid",
        "invalid-schema",
    ],
)
def test_evaluate_conformance(value, schema, is_negative, expected):
    result = evaluate_conformance(
        value=value,
        location=ParameterLocation.BODY,
        media_type="application/json",
        schema=schema,
        validator_cls=None,
        is_negative=is_negative,
    )
    if expected is None:
        assert result is None
        return
    assert result is not None
    assert dataclasses.replace(result, errors=()) == expected
    assert bool(result.errors) is expected.expected_valid


def _meta(location, *, mode=GenerationMode.POSITIVE):
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={location: ComponentInfo(mode=mode)},
        phase=PhaseInfo.coverage(scenario=CoverageScenario.DEFAULT_POSITIVE_TEST, description=""),
    )


BODY_OPERATION = {
    "/data": {
        "post": {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"kind": {"type": "string"}, "size": {"type": "integer"}},
                            "required": ["kind"],
                            "if": {"properties": {"kind": {"const": "big"}}, "required": ["kind"]},
                            "then": {"required": ["size"]},
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        }
    }
}
QUERY_OPERATION = {
    "/data": {
        "get": {
            "parameters": [{"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "OK"}},
        }
    }
}


# A conditional body counts as invalid only where the API's own validator enforces it.
@pytest.mark.parametrize(("version", "is_violation"), [("3.0.2", False), ("3.1.0", True)])
def test_body_judged_by_the_schema_production_validates_with(ctx, case_factory, version, is_violation):
    operation = ctx.openapi.load_schema(BODY_OPERATION, version=version)["/data"]["POST"]
    case = case_factory(operation=operation, method="POST", body={"kind": "big"}, _meta=_meta(ParameterLocation.BODY))

    violation = check_conformance(case)
    assert (violation is not None) is is_violation
    if violation is not None:
        assert violation.location == ParameterLocation.BODY
        assert violation.expected_valid


def test_non_body_container_is_checked(ctx, case_factory):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    meta = _meta(ParameterLocation.QUERY)
    meta.raw_containers[ParameterLocation.QUERY] = {"limit": "not-a-number"}
    case = case_factory(operation=operation, query={"limit": "not-a-number"}, _meta=meta)

    violation = check_conformance(case)
    assert violation is not None
    assert violation.location == ParameterLocation.QUERY
    assert violation.expected_valid


# On the wire an integer is a string and an object is spread over sibling keys.
def test_container_without_a_typed_snapshot_is_not_judged(ctx, case_factory):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    case = case_factory(operation=operation, query={"limit": "5"}, _meta=_meta(ParameterLocation.QUERY))

    assert check_conformance(case) is None


def test_ungenerated_container_is_not_judged(ctx, case_factory):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    meta = _meta(ParameterLocation.PATH)
    meta.raw_containers[ParameterLocation.QUERY] = {"limit": "not-a-number"}
    case = case_factory(operation=operation, query={"limit": "not-a-number"}, _meta=meta)

    assert check_conformance(case) is None


# Negative generation drops required parameters it cannot negate, leaving untouched containers incomplete.
def test_untouched_container_of_a_negative_case_is_not_judged(ctx, case_factory):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={ParameterLocation.QUERY: ComponentInfo(mode=GenerationMode.POSITIVE)},
        phase=PhaseInfo.coverage(scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD, description=""),
    )
    meta.raw_containers[ParameterLocation.QUERY] = {}
    case = case_factory(operation=operation, query={}, _meta=meta)

    assert check_conformance(case) is None


HEADER_OPERATION = {
    "/data": {
        "post": {
            "parameters": [{"name": "Content-Type", "in": "header", "required": True, "schema": {"type": "string"}}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
            "responses": {"200": {"description": "OK"}},
        }
    }
}


# A content-type probe negates the declared request media types, which a free-form header schema still admits.
@pytest.mark.parametrize(
    ("scenario", "content_type"),
    [
        (CoverageScenario.UNSUPPORTED_CONTENT_TYPE, "text/plain"),
        (CoverageScenario.MALFORMED_CONTENT_TYPE, "application.json"),
    ],
    ids=["unsupported", "malformed"],
)
def test_content_type_probe_header_is_not_judged(ctx, case_factory, scenario, content_type):
    operation = ctx.openapi.load_schema(HEADER_OPERATION)["/data"]["POST"]
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={ParameterLocation.HEADER: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo.coverage(scenario=scenario, description=""),
    )
    meta.raw_containers[ParameterLocation.HEADER] = {"Content-Type": content_type}
    case = case_factory(operation=operation, method="POST", headers={"Content-Type": content_type}, body={}, _meta=meta)

    assert check_conformance(case) is None


# Query values reach the server as text, so the string `"5"` reads as the integer a valid case would send.
@pytest.mark.parametrize(("value", "is_violation"), [("5", True), ("abc", False)])
def test_negative_container_is_judged_after_serialization(ctx, case_factory, value, is_violation):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    meta = _meta(ParameterLocation.QUERY, mode=GenerationMode.NEGATIVE)
    meta.raw_containers[ParameterLocation.QUERY] = {"limit": value}
    case = case_factory(operation=operation, query={"limit": value}, _meta=meta)

    violation = check_conformance(case)
    assert (violation is not None) is is_violation
    if violation is not None:
        assert violation.value == {"limit": 5}
        assert not violation.expected_valid
        assert violation.after_serialization


def test_positive_container_survives_serialization(ctx, case_factory):
    operation = ctx.openapi.load_schema(QUERY_OPERATION)["/data"]["GET"]
    meta = _meta(ParameterLocation.QUERY)
    meta.raw_containers[ParameterLocation.QUERY] = {"limit": 5}
    case = case_factory(operation=operation, query={"limit": 5}, _meta=meta)

    assert check_conformance(case) is None


ARRAY_QUERY_OPERATION = {
    "/data": {
        "get": {
            "parameters": [
                {
                    "name": "ids",
                    "in": "query",
                    "required": True,
                    "explode": False,
                    "schema": {"type": "array", "items": {"type": "integer"}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }
}


# A non-exploded array is sent comma-joined, a spelling the scalar reader does not undo.
def test_serialized_array_is_not_read_as_text(ctx, case_factory):
    operation = ctx.openapi.load_schema(ARRAY_QUERY_OPERATION)["/data"]["GET"]
    meta = _meta(ParameterLocation.QUERY)
    meta.raw_containers[ParameterLocation.QUERY] = {"ids": [1, 2]}
    case = case_factory(operation=operation, query={"ids": "1,2"}, _meta=meta)

    assert check_conformance(case) is None


def _operation(ctx, parameter):
    path = "/data/{x}" if parameter["in"] == "path" else "/data"
    paths = {path: {"get": {"parameters": [{"name": "x", **parameter}], "responses": {"200": {"description": "OK"}}}}}
    return ctx.openapi.load_schema(paths)[path]["GET"]


@pytest.mark.parametrize(
    ("parameter", "mode", "generated", "sent"),
    [
        (
            {"in": "path", "required": True, "schema": {"type": "string", "enum": ["a%20b"]}},
            GenerationMode.POSITIVE,
            "a%20b",
            "a%20b",
        ),
        (
            {
                "in": "path",
                "required": True,
                "content": {"application/json": {"schema": {"type": "string", "maxLength": 3}}},
            },
            GenerationMode.POSITIVE,
            "abc",
            "%22abc%22",
        ),
        (
            {"in": "query", "schema": {"type": "number", "maximum": 9223372036854776000}},
            GenerationMode.NEGATIVE,
            9223372036854776001,
            9223372036854776001,
        ),
    ],
    ids=["declared-encoded-path-value", "json-encoded-parameter", "integer-beyond-float-precision"],
)
def test_wire_reading_keeps_validity(ctx, case_factory, parameter, mode, generated, sent):
    operation = _operation(ctx, parameter)
    location = ParameterLocation(parameter["in"])
    meta = _meta(location, mode=mode)
    meta.raw_containers[location] = {"x": generated}
    container = {"path_parameters" if location == ParameterLocation.PATH else "query": {"x": sent}}
    case = case_factory(operation=operation, _meta=meta, **container)

    assert check_conformance(case) is None
