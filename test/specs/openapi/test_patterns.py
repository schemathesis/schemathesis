import re
from itertools import product

import jsonschema_rs
import pytest
from flask import jsonify
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.engine import from_schema
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import examples
from schemathesis.specs.openapi.converter import update_pattern_in_schema
from test.utils import run_given

REWRITE_ENUMERATION_MAX_LENGTH = 7


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "alphabet"),
    [
        (r"^([a-zA-Z0-9]*)$", 5, 5, "a0"),
        (r"^([a-zA-Z0-9_.-]+)$", 3, 12, "a."),
        (r"^(?i:[a-z]*)$", 4, 4, "aB"),
        (r"^(?i:([a-z-]+))$", 2, 9, "a-"),
        (r"^([a-zA-ZÀ-ÖØ-öø-ɏ \t\n\r\f\v0-9_.:/=+\-@]*)$", 256, 256, "a"),
        # Unanchored: the rewrite keeps only the strings that match end to end.
        (r"a{2}-+\.{2}", 7, 12, None),
        (r"^[a-z]{1,3}[0-9]+$", 5, 10, "a0"),
        (r"^(a?)+b+c+$", 7, 12, "abc"),
        (r"^(a|bb)+$", 4, 8, "ab"),
        (r"^[a-z]+[0-9]+$", 4, 8, "a0"),
    ],
)
@settings(max_examples=30, suppress_health_check=list(HealthCheck))
@given(data=st.data())
def test_group_rewrite_stays_within_the_original_language(pattern, min_length, max_length, alphabet, data):
    schema = {"type": "string", "pattern": pattern, "minLength": min_length, "maxLength": max_length}
    rewritten_schema = dict(schema)
    update_pattern_in_schema(rewritten_schema)
    original = jsonschema_rs.validator_for(schema, pattern_options=FANCY_REGEX_OPTIONS)
    folded = jsonschema_rs.validator_for(rewritten_schema, pattern_options=FANCY_REGEX_OPTIONS)
    value = data.draw(st.from_regex(rewritten_schema["pattern"], fullmatch=True, alphabet=st.characters(codec=None)))
    assert re.search(pattern, value), f"{value!r} does not match {pattern}"
    assert not folded.is_valid(value) or original.is_valid(value), f"{value!r} under {rewritten_schema}"
    if alphabet is None:
        return
    for length in range(min_length, min(max_length, REWRITE_ENUMERATION_MAX_LENGTH) + 1):
        for letters in product(alphabet, repeat=length):
            text = "".join(letters)
            assert original.is_valid(text) == folded.is_valid(text), f"{text!r} under {rewritten_schema}"


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        (
            {"pattern": r"^[a-z]+$", "minLength": 1, "maxLength": 10},
            {"type": "string", "pattern": r"^[a-z]{1,10}$"},
        ),
        # Unbounded `{1,}` survives the rewrite; `maxLength` must stay so length is still enforced.
        (
            {"pattern": r"^([a-z]+-){2,3}\d+$", "minLength": 1, "maxLength": 32},
            {"type": "string", "pattern": r"^([a-z]{1,}-){2,3}\d{1,28}$", "maxLength": 32},
        ),
        # Each slot's max is allocated as if it were the only one, so together they outrun the budget.
        (
            {"pattern": r"^0+0+$", "maxLength": 3},
            {"type": "string", "pattern": r"^0{1,2}0{1,2}$", "maxLength": 3},
        ),
        (
            {"pattern": r"^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\.?$", "minLength": 1, "maxLength": 30},
            {
                "type": "string",
                "pattern": r"^[a-zA-Z]{1,30}([ '\-][a-zA-Z]{1,}){0,2}\.{0,1}$",
                "maxLength": 30,
            },
        ),
    ],
)
def test_update_pattern_in_schema_keeps_unenforced_bounds(bounds, expected):
    schema = {"type": "string", **bounds}
    update_pattern_in_schema(schema)
    assert schema == expected


def test_pcre_pattern_in_response_schema_during_dependency_analysis(cli, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/owners": {
                "get": {
                    "operationId": "listOwners",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Owner"},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/owners/{ownerId}": {
                "get": {
                    "operationId": "getOwner",
                    "parameters": [{"name": "ownerId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        # allOf triggers canonicalize() call in dependency analysis
                                        "allOf": [
                                            {"$ref": "#/components/schemas/Owner"},
                                            {"description": "An owner"},
                                        ]
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
        components={
            "schemas": {
                "Owner": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        # PCRE Unicode property escape - this caused the crash
                        "firstName": {"type": "string", "pattern": r"^[\p{L}]+([ '-][\p{L}]+){0,2}\.?$"},
                        "lastName": {"type": "string", "pattern": r"^[\p{L}]+([ '-][\p{L}]+){0,2}\.?$"},
                    },
                }
            }
        },
    )

    @app.route("/owners")
    def list_owners():
        return jsonify([{"id": 1, "firstName": "John", "lastName": "Doe"}])

    @app.route("/owners/<int:owner_id>")
    def get_owner(owner_id):
        return jsonify({"id": owner_id, "firstName": "John", "lastName": "Doe"})

    # This should not crash with SchemaError about invalid regex
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=1",
            "--phases=examples",
        )
        == snapshot_cli
    )


def test_response_schema_is_not_mutated(cli, ctx, snapshot_cli):
    # See GH-2749
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "Container Image API", "version": "1.0.0"},
        "paths": {
            "/container": {
                "post": {
                    "summary": "Create a container image",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"container_image": {"$ref": "#/components/schemas/ContainerImage"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "container_image": {"$ref": "#/components/schemas/ContainerImage"}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "ContainerImage": {
                    "description": "A container image",
                    "type": "string",
                    "maxLength": 500,
                    "pattern": "^[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*(\\/[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*)*(:[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}|@sha256:[a-fA-F0-9]{64}){0,1}$",
                    "example": "renku/renkulab-py:3.10-0.18.1",
                }
            }
        },
    }

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/container", methods=["POST"])
    def create_container():
        example_value = raw_schema["components"]["schemas"]["ContainerImage"]["example"]
        response_body = {"container_image": example_value}
        return jsonify(response_body), 200

    assert cli.run_openapi_app(app, "-call", "--phases=fuzzing", "-n 1") == snapshot_cli


@pytest.mark.snapshot(replace_invalid_component=True)
def test_unicode_surrogate_patterns_are_dropped_with_warning(cli, ctx, snapshot_cli):
    # Surrogate code points are invalid in regex engines.
    query_pattern = "([\\u0020-\\uD7FF\\uE000-\\uFFFD\\uD800\\uDBFF-\\uDC00\\uDFFF\\t\\r\\n]){0,255}"
    body_pattern = "[\\uD800-\\uDBFF]"

    app, _ = ctx.openapi.make_flask_app(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "description",
                            "in": "query",
                            "schema": {"type": "string", "pattern": query_pattern},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string", "pattern": body_pattern},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    )

    @app.route("/test", methods=["GET", "POST"])
    def test_endpoint():
        return jsonify({"status": "ok"})

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=10",
            "--phases=fuzzing",
        )
        == snapshot_cli
    )


def test_pattern_the_validator_cannot_compile_is_dropped(ctx):
    # Such a pattern constrains nothing during validation, and keeping it would only stop the rest
    # of the schema from being modeled.
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "schema": {"type": "string", "pattern": "[A-Z]{,3}", "maxLength": 5},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    parameter = next(iter(operation.query))

    assert "pattern" not in parameter.optimized_schema
    assert parameter.optimized_schema["maxLength"] == 5


@pytest.mark.parametrize("pattern", [r"^(?P<major>\d+)$", r"^(?<major>\d+)$"], ids=["python", "ecma"])
def test_named_group_pattern_is_kept(ctx, pattern):
    # A group name only one engine reads would leave the pattern refused where the schema is validated.
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "pattern": pattern}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert next(iter(operation.query)).optimized_schema["pattern"] == r"^(\d+)$"
    assert examples.generate_one(operation.as_strategy()).query["q"].isdigit()


LETTERS = r"^[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+$"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (r"^[\p{L}]+$", LETTERS),
        (r"^[\p{L]+$", None),
        (r"^[\pL]+$", LETTERS),
        (r"^[\pQ]\pL$", None),
        (r"^[[:alpha:]]+$", LETTERS),
        (r"^[[:digit:]]x[[:alpha]$", r"^[0-9]x[\[:alpha]$"),
        (r"^(?<name>[a-z])+$", r"^([a-z])+$"),
        (r"^(?<name[a-z]+$", None),
    ],
    ids=[
        "braced-property",
        "unclosed-braced-property",
        "shorthand-property",
        "unknown-shorthand-property",
        "posix-class",
        "unclosed-posix-class",
        "named-group",
        "unclosed-named-group",
    ],
)
def test_pcre_class_syntax_is_translated_or_dropped(ctx, pattern, expected):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "pattern": pattern}}
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert next(iter(operation.query)).optimized_schema.get("pattern") == expected


def test_quantifier_rewrite_the_validator_cannot_compile_is_not_taken():
    # Folding a bound this large into the pattern makes a quantifier the regex engine refuses; the
    # pattern and the bound both stay as they were.
    schema = {"type": "string", "pattern": "^.{1,}[0-9]$", "maxLength": 2147483647}

    update_pattern_in_schema(schema)

    assert schema == {"type": "string", "pattern": "^.{1,}[0-9]$", "maxLength": 2147483647}


@pytest.mark.parametrize("length_keyword", ["minLength", "maxLength"], ids=["min", "max"])
def test_huge_length_is_not_folded_into_pattern(ctx, length_keyword):
    length = 9223372036854775807
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [
                {
                    "name": "q",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^a+$", length_keyword: length},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    assert next(iter(operation.query)).optimized_schema == {
        "type": "string",
        "pattern": "^a+$",
        length_keyword: length,
    }
    if length_keyword == "maxLength":
        value = examples.generate_one(operation.as_strategy()).query["q"]
        assert re.fullmatch("^a+$", value)
        assert len(value) <= length


def test_quantifier_rewrite_within_reach_is_taken():
    schema = {"type": "string", "pattern": "^.{1,}$", "maxLength": 10}

    update_pattern_in_schema(schema)

    assert schema == {"type": "string", "pattern": "^.{1,10}$"}


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    ("parameter_schema", "expected"),
    [
        (
            {"type": "string", "pattern": "^a+", "minLength": 3},
            {"type": "string", "pattern": "^a{3,}"},
        ),
        (
            {"type": "string", "pattern": "^(ab|c)", "maxLength": 5},
            {"type": "string", "pattern": "^(ab|c)", "maxLength": 5},
        ),
        (
            {"type": "string", "pattern": "(ab|c)$", "maxLength": 5},
            {"type": "string", "pattern": "(ab|c)$", "maxLength": 5},
        ),
        (
            {"type": "string", "pattern": "^(?:a|bb){1,2}$", "minLength": 1, "maxLength": 10},
            {"type": "string", "pattern": "^(?:a|bb){1,2}$", "minLength": 1, "maxLength": 10},
        ),
        (
            {"type": "string", "pattern": "^(?:(?:a|bb){2}){1,3}$", "minLength": 1, "maxLength": 100},
            {"type": "string", "pattern": "^(?:(?:a|bb){2}){1,3}$", "minLength": 1, "maxLength": 100},
        ),
        (
            {"type": "string", "pattern": "^(?:(?:ab){2}c){1,3}$", "minLength": 1, "maxLength": 100},
            {"type": "string", "pattern": "^(?:(?:ab){2}c){1,3}$", "minLength": 1, "maxLength": 100},
        ),
        (
            {"type": "string", "pattern": "^(?:(ab)c){1,3}$", "minLength": 1, "maxLength": 100},
            {"type": "string", "pattern": "^(?:(ab)c){1,3}$", "minLength": 1, "maxLength": 100},
        ),
        (
            {"type": "string", "pattern": "^x(?:a|bb)+$", "minLength": 5, "maxLength": 5},
            {"type": "string", "pattern": "^x(?:a|bb)+$", "minLength": 5, "maxLength": 5},
        ),
        (
            {"type": "string", "pattern": "^a{1,2}b{1,2}c{3}$", "minLength": 7, "maxLength": 7},
            {"type": "string", "pattern": "^a{2}b{2}c{3}$"},
        ),
    ],
    ids=[
        "leading-anchor-floor",
        "leading-anchor-group",
        "trailing-anchor-group",
        "repeated-alternation",
        "fixed-count-alternation",
        "fixed-count-group",
        "capturing-group-inside-repeat",
        "exact-length-alternation",
        "exact-length-searched",
    ],
)
def test_length_bounds_beside_a_pattern(ctx, parameter_schema, expected):
    operation = ctx.openapi.load_operation(
        "GET /items",
        {
            "parameters": [{"name": "q", "in": "query", "required": True, "schema": parameter_schema}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert next(iter(operation.query)).optimized_schema == expected
    validator = jsonschema_rs.validator_for(parameter_schema)

    @run_given(case=operation.as_strategy(), max_examples=10)
    def check(case):
        assert validator.is_valid(case.query["q"]), case.query


@pytest.mark.parametrize(
    ("pattern", "meaning"),
    [("[^ab]x", "[^ab]x"), ("[[:alpha:]]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+")],
    ids=["negated-class", "posix-class"],
)
def test_coverage_phase_header_values_match_pattern(ctx, app_runner, pattern, meaning):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "X-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "pattern": pattern},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.phases.update(phases=["coverage"])
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    values = []

    @schemathesis.hook
    def before_call(context, case, **kwargs):
        if case.meta.generation.mode == GenerationMode.POSITIVE:
            values.append(case.headers["X-Key"])

    for _ in from_schema(schema).execute():
        pass
    validator = jsonschema_rs.validator_for({"type": "string", "pattern": meaning})
    assert values
    assert all(validator.is_valid(value) for value in values), values
