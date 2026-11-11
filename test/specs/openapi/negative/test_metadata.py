import pytest
from hypothesis import given, settings
from hypothesis.database import InMemoryExampleDatabase

import schemathesis
from schemathesis.core.mutations import Mutation, MutationChannel, MutationMetadata, OperatorKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from test.utils import find_first

OWNER = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "pattern": "^[a-z]+$"}}}
TAG = {"type": "string", "minLength": 2}
BOUND = {"type": "integer", "minimum": 1}


def _mutation(
    *,
    schema_pointer: str = "",
    keywords: tuple[str, ...] = ("type",),
    original_value: object = None,
    new_value: object = None,
    operator: OperatorKind = OperatorKind.CHANGE_TYPE,
    parameter: str | None = None,
    location: ParameterLocation = ParameterLocation.QUERY,
) -> Mutation:
    return Mutation(
        path=(),
        parameter_location=location,
        schema_pointer=schema_pointer,
        channel=MutationChannel.SCHEMA,
        operator=operator,
        keywords=keywords,
        parameter=parameter,
        original_value=original_value,
        new_value=new_value,
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            _mutation(
                schema_pointer="",
                keywords=("type",),
                original_value="object",
                new_value="integer",
            ),
            "violates `type` (was object, became integer)",
        ),
        (
            _mutation(
                schema_pointer="/properties/user/properties/email",
                keywords=("type",),
                original_value="string",
                new_value="integer",
            ),
            "violates `type` at /properties/user/properties/email (was string, became integer)",
        ),
        (
            _mutation(
                schema_pointer="/properties/x",
                keywords=("type",),
                original_value="integer | number",
                new_value="string",
            ),
            "violates `type` at /properties/x (was integer | number, became string)",
        ),
        (
            _mutation(
                schema_pointer="",
                keywords=("required",),
                operator=OperatorKind.NEGATE_CONSTRAINTS,
                original_value=["email", "name"],
                new_value=None,
            ),
            "violates `required` (was email, name)",
        ),
        (
            _mutation(
                schema_pointer="",
                keywords=("minLength",),
                operator=OperatorKind.NEGATE_CONSTRAINTS,
            ),
            "violates `minLength`",
        ),
        (
            _mutation(
                schema_pointer="/properties/name",
                keywords=("minLength", "pattern"),
                operator=OperatorKind.NEGATE_CONSTRAINTS,
            ),
            "violates `minLength`, `pattern` at /properties/name",
        ),
        (
            _mutation(
                schema_pointer="/properties/email",
                keywords=("required",),
                operator=OperatorKind.REMOVE_REQUIRED_PROPERTY,
                parameter="email",
            ),
            "violates `required` at /properties/email",
        ),
        (
            _mutation(
                schema_pointer="/properties/age",
                keywords=("minimum",),
                operator=OperatorKind.VALUE_VIOLATOR,
                original_value=18,
                new_value=17,
            ),
            "violates `minimum` at /properties/age (was 18, became 17)",
        ),
        (
            _mutation(
                schema_pointer="/properties/email",
                keywords=("format:email",),
                operator=OperatorKind.VALUE_VIOLATOR,
                original_value="user@example.com",
                new_value="useratexample.com",
            ),
            'violates `format:email` at /properties/email (was "user@example.com", became "useratexample.com")',
        ),
        # Dict-valued `became` is suppressed to keep the message readable.
        (
            _mutation(
                schema_pointer="/properties/profile",
                keywords=("required",),
                operator=OperatorKind.VALUE_VIOLATOR,
                original_value="alice@example.com",
                new_value={"name": "Alice", "id": 42},
            ),
            'violates `required` at /properties/profile (was "alice@example.com")',
        ),
    ],
)
def test_description_rendering(mutation, expected):
    assert MutationMetadata((mutation,)).description == expected


def test_description_multi_mutation_renders_bulleted():
    type_change = _mutation(
        schema_pointer="/properties/user/properties/email",
        keywords=("type",),
        original_value="string",
        new_value="integer",
    )
    min_length_negation = _mutation(
        schema_pointer="/properties/password",
        keywords=("minLength",),
        operator=OperatorKind.NEGATE_CONSTRAINTS,
    )
    assert (
        MutationMetadata((type_change, min_length_negation)).description
        == "- violates `type` at /properties/user/properties/email (was string, became integer)\n"
        "- violates `minLength` at /properties/password"
    )


def test_description_empty_mutations_returns_none():
    assert MutationMetadata(()).description is None


def test_syntax_fuzzing_describes_the_payload():
    # Random bytes violate no keyword, so there is nothing to render from the schema.
    mutation = _mutation(schema_pointer="", keywords=(), operator=OperatorKind.SYNTAX_FUZZING)
    assert MutationMetadata((mutation,)).description == "Invalid syntax: random bytes"


def test_description_names_each_location_when_they_differ():
    assert MutationMetadata(
        (
            _mutation(schema_pointer="/properties/key", keywords=("type",), location=ParameterLocation.QUERY),
            _mutation(schema_pointer="/properties/X-Key", keywords=("minimum",), location=ParameterLocation.HEADER),
        )
    ).description == ("- query: violates `type` at /properties/key\n- header: violates `minimum` at /properties/X-Key")


def test_mutations_cover_every_negated_location(ctx):
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "parameters": [
                {
                    "name": "X-Token",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 5},
                },
                {"name": "offset", "in": "query", "schema": {"type": "integer"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    # A single case has to negate both containers for the omission to surface, hence the wider search.
    @given(operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=100, deadline=None, database=InMemoryExampleDatabase())
    def test(case):
        if "X-Token" in (case.headers or {}):
            return
        description = case.meta.phase.data.description or ""
        assert "X-Token" in description, description

    test()


def _load_pets(ctx, version, variant):
    prefix = "#/definitions/" if version == "2.0" else "#/components/schemas/"
    pet = {
        "type": "object",
        "required": ["owner", "tags", "meta"],
        "properties": {
            "owner": {"$ref": "owner.json" if variant == "external-file" else f"{prefix}Owner"},
            "tags": {"type": "array", "minItems": 1, "items": {"$ref": f"{prefix}Tag"}},
            "meta": {"type": "object", "minProperties": 1, "additionalProperties": {"$ref": f"{prefix}Tag"}},
        },
    }
    responses = {"200": {"description": "OK"}}
    if version == "2.0":
        definition = {
            "parameters": [{"in": "body", "name": "body", "required": True, "schema": {"$ref": f"{prefix}Pet"}}],
            "responses": responses,
        }
        components = {"definitions": {"Pet": pet, "Owner": OWNER, "Tag": TAG}}
    else:
        body = {"schema": {"$ref": f"{prefix}Pet"}}
        if variant == "encoded-multipart":
            content = {
                "multipart/form-data": {**body, "encoding": {"tags": {"contentType": "text/plain, application/json"}}}
            }
        else:
            content = {"application/json": body}
        definition = {
            "parameters": [
                {"in": location, "name": name, "required": True, "schema": {"$ref": f"{prefix}Limit"}}
                for location, name in (("query", "limit"), ("header", "X-Limit"), ("path", "id"))
            ],
            "requestBody": {"required": True, "content": content},
            "responses": responses,
        }
        limit = {"allOf": [{"$ref": f"{prefix}Bound"}]}
        components = {
            "components": {"schemas": {"Pet": pet, "Owner": OWNER, "Tag": TAG, "Limit": limit, "Bound": BOUND}}
        }
    paths = {"/pets/{id}": {"post": definition}} if version != "2.0" else {"/pets": {"post": definition}}
    if variant != "external-file":
        return ctx.openapi.load_schema(paths, version=version, **components)
    ctx.makefile(OWNER, filename="owner")
    return schemathesis.openapi.from_path(str(ctx.openapi.write_schema(paths, version=version, **components)))


@pytest.mark.parametrize(
    ("version", "variant", "channel", "location", "path", "keyword", "expected"),
    [
        ("3.0.2", None, "value", "body", ("owner", "name"), "pattern", "/components/schemas/Owner/properties/name"),
        ("3.0.2", None, "value", "body", ("owner",), "required", "/components/schemas/Owner"),
        ("3.0.2", None, "value", "body", ("tags",), "minLength", "/components/schemas/Tag"),
        ("3.0.2", None, "value", "body", ("meta",), "minLength", "/components/schemas/Tag"),
        ("2.0", None, "value", "body", ("owner", "name"), "pattern", "/definitions/Owner/properties/name"),
        ("3.1.0", None, "value", "body", ("owner", "name"), "pattern", "/components/schemas/Owner/properties/name"),
        ("3.0.2", "external-file", "value", "body", ("owner", "name"), "pattern", "/owner.json/properties/name"),
        ("3.0.2", None, "schema", "body", ("owner", "name"), "pattern", "/components/schemas/Owner/properties/name"),
        ("2.0", None, "schema", "body", ("owner", "name"), "pattern", "/definitions/Owner/properties/name"),
        ("3.1.0", None, "schema", "body", ("owner", "name"), "pattern", "/components/schemas/Owner/properties/name"),
        ("3.0.2", "external-file", "schema", "body", ("owner", "name"), "pattern", "/owner.json/properties/name"),
        (
            "3.0.2",
            "encoded-multipart",
            "value",
            "body",
            ("owner", "name"),
            "pattern",
            "/components/schemas/Owner/properties/name",
        ),
        (
            "3.0.2",
            "encoded-multipart",
            "schema",
            "body",
            ("owner", "name"),
            "pattern",
            "/components/schemas/Owner/properties/name",
        ),
        ("3.0.2", None, "schema", "query", ("limit",), "minimum", "/components/schemas/Bound"),
        ("3.0.2", None, "schema", "header", ("X-Limit",), "minimum", "/components/schemas/Bound"),
        ("3.0.2", None, "schema", "path", ("id",), "minimum", "/components/schemas/Bound"),
    ],
    ids=[
        "value-nested-leaf",
        "value-required",
        "value-items",
        "value-additional-properties",
        "value-swagger-2.0",
        "value-openapi-3.1",
        "value-external-file",
        "schema-nested-leaf",
        "schema-swagger-2.0",
        "schema-openapi-3.1",
        "schema-external-file",
        "value-encoded-multipart",
        "schema-encoded-multipart",
        "schema-query",
        "schema-header",
        "schema-path",
    ],
)
def test_mutation_location_names_definition_behind_reference(
    ctx, version, variant, channel, location, path, keyword, expected
):
    schema = _load_pets(ctx, version, variant)
    operation = next(schema.get_all_operations()).ok()

    def is_target(mutation):
        return (
            mutation.channel == channel
            and mutation.parameter_location == location
            and mutation.path[: len(path)] == path
            and keyword in mutation.keywords
        )

    case = find_first(
        operation.as_strategy(generation_mode=GenerationMode.NEGATIVE),
        lambda case: any(is_target(mutation) for mutation in case.meta.phase.data.mutations),
        # Each draw breaks one of several values, so the rarest target needs a wide search.
        max_examples=3000,
    )
    assert [mutation.schema_pointer for mutation in case.meta.phase.data.mutations if is_target(mutation)] == [expected]
