"""End-to-end tests for the constants overlay.

Drives the substitution via `operation.as_strategy(constants_value_source=...)` so the
whole strategy build path — caching, dictionary/semantic overlays, the constants overlay,
serialization filters — is exercised the way the engine exercises it. Manually building
internal data structures here would let dead wiring stay invisible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.python._constants.adapters import default_adapters
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.pool import ConstantsValueSource
from schemathesis.python._constants.registry import SourceRegistry

FIXTURES = Path(__file__).parent.parent.parent / "python" / "_constants" / "fixtures"


@pytest.fixture
def constants_source():
    sys.path.insert(0, str(FIXTURES))
    try:
        registry = SourceRegistry()

        @registry.decorator
        def from_sample():
            return ["sample_pkg.values"]

        result = extract_all(registry=registry, adapters=default_adapters())
        assert not result.pool.is_empty(), "fixture sanity: extraction returned an empty pool"
        yield ConstantsValueSource(result.pool)
    finally:
        sys.path.remove(str(FIXTURES))
        for name in list(sys.modules):
            if name == "sample_pkg" or name.startswith("sample_pkg."):
                sys.modules.pop(name)


@pytest.mark.hypothesis_nested
def test_constants_overlay_substitutes_string_property(ctx, constants_source):
    # The body's `status` is a plain string property; constants from the registered source
    # ("active", "inactive") should appear among generated cases.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"status": {"type": "string"}},
                                    "required": ["status"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    @given(case=operation.as_strategy(constants_value_source=constants_source))
    @settings(max_examples=80, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen.add(case.body["status"])

    collect()

    # At least one literal from the fixture must surface; the overlay's 30% probability
    # plus 80 draws makes the expected appearance count comfortably > 0.
    assert seen & {"active", "inactive"}, f"no constants substituted in {len(seen)} draws"


@pytest.mark.hypothesis_nested
def test_constants_overlay_preserves_container_oneof(ctx, constants_source):
    # `status` and `role` are linked by `oneOf`: the only valid combinations are
    # (active, user) and (banned, admin). The pool contains "active" and "inactive";
    # substituting `status` alone risks breaking the constraint when the substituted
    # value is in the pool but doesn't match the partner field. Every drawn case must
    # still satisfy the full body schema.
    body_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["active", "banned"]},
            "role": {"type": "string", "enum": ["user", "admin"]},
        },
        "required": ["status", "role"],
        "oneOf": [
            {"properties": {"status": {"const": "active"}, "role": {"const": "user"}}},
            {"properties": {"status": {"const": "banned"}, "role": {"const": "admin"}}},
        ],
    }
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/items"]["POST"]
    validator = jsonschema_rs.Draft202012Validator(body_schema)

    @given(case=operation.as_strategy(constants_value_source=constants_source))
    @settings(max_examples=60, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def check(case):
        assert validator.is_valid(case.body), f"overlay produced an invalid body: {case.body!r}"

    check()


@pytest.mark.hypothesis_nested
def test_constants_overlay_substitutes_integer_into_number_property(ctx, constants_source):
    # `type: "number"` accepts ints per JSON Schema; the pool's `LARGE_THRESHOLD = 12345`
    # must reach a number-typed property.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"limit": {"type": "number"}},
                                    "required": ["limit"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schema["/items"]["POST"]
    seen: set[float] = set()

    @given(case=operation.as_strategy(constants_value_source=constants_source))
    @settings(max_examples=80, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen.add(case.body["limit"])

    collect()

    assert 12345 in seen, f"integer constant never substituted into a number property: {seen!r}"
