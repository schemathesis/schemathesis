from __future__ import annotations

import jsonschema_rs
import pytest

from schemathesis.config import SchemathesisConfig
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from test.utils import run_given


def _load_schema(ctx, config: dict, paths: dict, *, version: str = "3.0.2"):
    schema = ctx.openapi.load_schema(paths, version=version)
    parent_config = SchemathesisConfig.from_dict(config)
    schema.config._parent = parent_config
    schema.config.generation = parent_config.projects.default.generation
    schema.config.parameters = parent_config.projects.default.parameters
    schema.config.operations = parent_config.projects.default.operations
    return schema


def _path_with_body(body_schema: dict, *, required: bool = True) -> dict:
    body: dict = {"content": {"application/json": {"schema": body_schema}}}
    if required:
        body["required"] = True
    return {
        "/items": {
            "post": {
                "requestBody": body,
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


@pytest.mark.hypothesis_nested
def test_top_level_literal_substitutes_every_case(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.ccNumber": "1234-5678-9012-3456"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {"ccNumber": {"type": "string"}, "other": {"type": "string"}},
                "required": ["ccNumber"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    seen.update(case.body.get("ccNumber") for case in generate_many(operation.as_strategy(), 10))
    assert seen == {"1234-5678-9012-3456"}


@pytest.mark.hypothesis_nested
def test_nested_literal_substitutes_every_case(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.user.email": "pinned@example.com"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": ["email"],
                    }
                },
                "required": ["user"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    seen.update(case.body["user"].get("email") for case in generate_many(operation.as_strategy(), 10))
    assert seen == {"pinned@example.com"}


@pytest.mark.hypothesis_nested
def test_nested_literal_replaces_null_parent(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.user.email": "pinned@example.com"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "nullable": True,
                        "properties": {"email": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
                "required": ["user"],
                "additionalProperties": False,
            }
        ),
    )
    operation = schema["/items"]["POST"]
    bodies = [case.body for case in generate_many(operation.as_strategy(), 10)]
    assert bodies == [{"user": {"email": "pinned@example.com"}}] * 10


@pytest.mark.hypothesis_nested
def test_force_inserts_missing_optional_leaf(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.optional_token": "FORCED"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "optional_token": {"type": "string"},
                },
                "required": ["name"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    cases_seen = 0
    cases_with_force = 0

    @run_given(case=operation.as_strategy(), max_examples=15, derandomize=True)
    def collect(case):
        nonlocal cases_seen, cases_with_force
        if not isinstance(case.body, dict):
            return
        cases_seen += 1
        if case.body.get("optional_token") == "FORCED":
            cases_with_force += 1

    assert cases_seen > 0
    assert cases_with_force == cases_seen, "optional leaf should be force-inserted in every case"


@pytest.mark.hypothesis_nested
def test_wildcard_substitutes_every_array_element(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.tags[*]": "TAG"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                },
                "required": ["tags"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    total = 0
    matched = 0

    @run_given(case=operation.as_strategy(), max_examples=10, derandomize=True)
    def collect(case):
        nonlocal total, matched
        for item in case.body["tags"]:
            total += 1
            if item == "TAG":
                matched += 1

    assert total > 0 and matched == total


@pytest.mark.hypothesis_nested
def test_op_scope_literal_beats_global_dict_binding(ctx):
    schema = _load_schema(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["DICT_VALUE"]}},
            "parameters": {"body.token": {"dictionary": "vals"}},
            "operations": [
                {
                    "include-name": "POST /items",
                    "parameters": {"body.token": "OP_LITERAL"},
                }
            ],
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    seen.update(case.body.get("token") for case in generate_many(operation.as_strategy(), 10))
    assert seen == {"OP_LITERAL"}, "op-scoped literal must veto global dict binding"


@pytest.mark.hypothesis_nested
def test_op_scope_dict_binding_beats_global_literal(ctx):
    schema = _load_schema(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["DICT_VALUE"]}},
            "parameters": {"body.token": "GLOBAL_LITERAL"},
            "operations": [
                {
                    "include-name": "POST /items",
                    "parameters": {"body.token": {"dictionary": "vals"}},
                }
            ],
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    seen.update(case.body.get("token") for case in generate_many(operation.as_strategy(), 10))
    assert seen == {"DICT_VALUE"}, "op-scoped dict binding must veto global literal"


@pytest.mark.hypothesis_nested
def test_literal_skipped_when_path_does_not_resolve(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.notInSchema": "leak"}},
        _path_with_body({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    )
    operation = schema["/items"]["POST"]
    leaks = 0

    @run_given(case=operation.as_strategy(), max_examples=10, derandomize=True)
    def collect(case):
        nonlocal leaks
        if "notInSchema" in (case.body or {}):
            leaks += 1

    assert leaks == 0


@pytest.mark.hypothesis_nested
def test_override_drops_stale_mutation_on_same_path(ctx):
    schema = _load_schema(
        ctx,
        {
            "parameters": {"body.token": "valid-token-value"},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "token": {"type": "string", "minLength": 5},
                    "sibling": {"type": "string", "minLength": 5},
                },
                "required": ["token", "sibling"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    saw_sibling_mutation = False

    @run_given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=40, derandomize=True)
    def collect(case):
        nonlocal saw_sibling_mutation
        if case._meta is None:
            return
        for mutation in case._meta.phase.data.mutations:
            assert not (mutation.path and mutation.path[:1] == ("token",)), (
                f"mutation on overridden /token must be dropped: path={mutation.path!r}"
            )
            if mutation.path and mutation.path[:1] == ("sibling",):
                saw_sibling_mutation = True

    assert saw_sibling_mutation, "no sibling mutation seen in any case; absence-of-token-mutation could be vacuous"


@pytest.mark.hypothesis_nested
def test_override_that_restores_a_valid_body_drops_its_mutations(ctx):
    body_schema = {"type": "object", "properties": {"token": {"type": "string", "minLength": 5}}, "required": ["token"]}
    schema = _load_schema(ctx, {"parameters": {"body.token": "valid-token-value"}}, _path_with_body(body_schema))
    validator = jsonschema_rs.Draft4Validator(body_schema)
    cases = generate_many(schema["/items"]["POST"].as_strategy(generation_mode=GenerationMode.NEGATIVE), 10)
    valid = [case for case in cases if isinstance(case.body, dict) and validator.is_valid(case.body)]
    assert valid
    assert [case.body for case in valid if case._meta.generation.mode.is_negative] == []


@pytest.mark.hypothesis_nested
def test_wildcard_override_does_not_corrupt_missing_optional_array(ctx):
    schema = _load_schema(
        ctx,
        {"parameters": {"body.tags[*]": "TAG"}},
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    corrupt = 0
    cases_seen = 0

    @run_given(case=operation.as_strategy(), max_examples=20, derandomize=True)
    def collect(case):
        nonlocal corrupt, cases_seen
        if not isinstance(case.body, dict):
            return
        cases_seen += 1
        tags = case.body.get("tags")
        if tags is not None and not isinstance(tags, list):
            corrupt += 1

    assert cases_seen > 0
    assert corrupt == 0, "wildcard override must not synthesize a non-array value for missing optional array"
