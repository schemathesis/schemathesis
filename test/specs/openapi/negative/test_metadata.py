import pytest
from hypothesis import Phase, find, given, settings
from hypothesis.database import InMemoryExampleDatabase

from schemathesis.core.mutations import Mutation, MutationChannel, MutationMetadata, OperatorKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from test.utils import run_given


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


NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {
            "type": "object",
            "patternProperties": {
                "^x": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"b": {"minimum": 1}}, "required": ["b"]},
                },
            },
        }
    },
}


def _resolve(schema, pointer):
    assert pointer == "" or pointer.startswith("/"), pointer
    node = schema
    for segment in pointer.split("/")[1:]:
        node = node[segment]
    return node


def _check_reported_location(mutation):
    # Raises when the pointer names no node of the declared schema.
    _resolve(NESTED_SCHEMA, mutation.schema_pointer)
    segments = mutation.schema_pointer.split("/")[1:]
    names = tuple(segments[index + 1] for index, segment in enumerate(segments[:-1]) if segment == "properties")
    # A removed property is named in the pointer but is not part of the path to the changed object.
    path = names[:-1] if mutation.operator == OperatorKind.REMOVE_REQUIRED_PROPERTY else names
    assert (mutation.parameter_location, mutation.path) == (ParameterLocation.BODY, path)
    if "required" not in mutation.keywords or mutation.operator == OperatorKind.REMOVE_REQUIRED_PROPERTY:
        assert mutation.parameter == (names[-1] if names else None)
    if mutation.operator == OperatorKind.CHANGE_TYPE:
        assert mutation.new_value in {"string", "integer", "number", "object", "array", "boolean", "null"}


def _schema_mutations(case):
    return [
        mutation
        for mutation in case.meta.phase.data.mutations
        if mutation.channel == MutationChannel.SCHEMA and mutation.operator != OperatorKind.SYNTAX_FUZZING
    ]


def test_mutations_are_reported_from_the_body_schema_root(ctx):
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {"required": True, "content": {"application/json": {"schema": NESTED_SCHEMA}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    strategy = operation.as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @run_given(strategy, max_examples=10)
    def check(case):
        for mutation in _schema_mutations(case):
            _check_reported_location(mutation)

    # Removing a property and changing the root type are reported differently from other mutations.
    for is_wanted in (
        lambda mutation: mutation.operator == OperatorKind.REMOVE_REQUIRED_PROPERTY,
        lambda mutation: mutation.operator == OperatorKind.CHANGE_TYPE and mutation.path == (),
    ):
        found = find(
            strategy,
            lambda case, is_wanted=is_wanted: any(is_wanted(mutation) for mutation in _schema_mutations(case)),
            # About one case in twelve removes a property; any match will do, so skip shrinking.
            settings=settings(max_examples=500, database=None, phases=[Phase.generate]),
        )
        for mutation in _schema_mutations(found):
            _check_reported_location(mutation)
