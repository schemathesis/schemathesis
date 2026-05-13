import pytest

from schemathesis.core.mutations import Mutation, MutationChannel, OperatorKind
from schemathesis.specs.openapi.negative.mutations import MutationMetadata


def _mutation(
    *,
    schema_pointer: str = "",
    keywords: tuple[str, ...] = ("type",),
    original_value: object = None,
    new_value: object = None,
    operator: OperatorKind = OperatorKind.CHANGE_TYPE,
    parameter: str | None = None,
) -> Mutation:
    return Mutation(
        path=(),
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


def test_description_override_takes_precedence():
    mutation = _mutation(schema_pointer="", keywords=("type",))
    assert (
        MutationMetadata((mutation,), description="Invalid syntax: random bytes").description
        == "Invalid syntax: random bytes"
    )
