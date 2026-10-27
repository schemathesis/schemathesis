import graphql
import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CoverageScenario, PhaseInfo
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS, UnknownScalar

DEFAULT = CoverageScenario.DEFAULT_POSITIVE_TEST


@pytest.fixture(autouse=True)
def clear_custom_scalars():
    yield
    CUSTOM_SCALARS.clear()


def iter_cases(schema, label, *, modes=(GenerationMode.POSITIVE,)):
    return list(
        schema.iter_coverage_cases(
            schema.find_operation_by_label(label),
            generation_modes=list(modes),
            generation_config=schema.config.generation,
        )
    )


def doc(text):
    # graphql-core versions print documents differently; compare against the installed printer.
    return graphql.print_ast(graphql.parse(text))


def bodies(schema, label, **kwargs):
    return [(case.meta.phase.data.scenario, case.body) for case in iter_cases(schema, label, **kwargs)]


@pytest.mark.parametrize(
    ("sdl", "label", "expected"),
    [
        (
            """
            type Book { id: ID! title: String related(limit: Int!): [Book] }
            type Query { book(id: ID!, n: Int!, x: Float!, s: String!, b: Boolean!): Book }
            """,
            "Query.book",
            '{ book(id: 1, n: 0, x: 0, s: "", b: true) { id title } }',
        ),
        ("type Query { f(a: Int, b: Int! = 10, c: Int!): Int }", "Query.f", "{ f(c: 0) }"),
        ("type Query { ping: Int } type Mutation { add(n: Int!): Int }", "Mutation.add", "mutation { add(n: 0) }"),
        (
            """
            enum Genre { DRAMA COMEDY }
            input Address { zip: String! city: String }
            input Filter { age: Int! genre: Genre! address: Address! tags: [String!]! note: String }
            type Query { users(filter: Filter!, ids: [ID!]!): Int }
            """,
            "Query.users",
            '{ users(filter: {age: 0, genre: DRAMA, address: {zip: ""}, tags: [""]}, ids: [1]) }',
        ),
        (
            "input F { and: [F!], x: Int, req: String! } input C { c: [C!]! } type Query { f(f: F!, c: C!): Int }",
            "Query.f",
            '{ f(f: {req: ""}, c: {c: []}) }',
        ),
        (
            "scalar FooBar input Filter { x: FooBar n: Int! } type Item { id: ID! } "
            "type Query { f(a: FooBar, filter: Filter!): Item }",
            "Query.f",
            "{ f(filter: {n: 0}) { id } }",
        ),
        ("type Query { f: String }", "Query.f", "{ f }"),
        ("type Query { f: [Genre!] } enum Genre { A }", "Query.f", "{ f }"),
        ("type Node { child: Node } type Query { f: Node }", "Query.f", "{ f { __typename } }"),
        (
            "type A { a: Int } type B { b: Int } union U = A | B type Query { f: U }",
            "Query.f",
            "{ f { __typename } }",
        ),
        (
            "interface Named { name: String id: ID! other: Named } type Query { f: [Named!]! }",
            "Query.f",
            "{ f { name id } }",
        ),
    ],
    ids=[
        "scalar-arguments",
        "optional-and-defaulted-arguments-omitted",
        "mutation",
        "input-objects",
        "recursive-inputs",
        "unknown-scalar-outside-required-arguments",
        "scalar-return",
        "enum-list-return",
        "object-without-scalar-fields",
        "union",
        "interface",
    ],
)
def test_default_case(ctx, sdl, label, expected):
    schema = ctx.graphql.load_sdl(sdl)
    assert bodies(schema, label) == [(DEFAULT, doc(expected))]


def test_no_cases_for_required_input_cycle(ctx):
    # Introspection results are not validated, so a cycle the SDL loader would reject can still arrive.
    raw = graphql.introspection_from_schema(
        graphql.build_schema(
            "input C { c: C! } input W { w: C! } type Query { f(c: C!): Int g(w: W!): Int ping: Int }",
            assume_valid=True,
        )
    )
    schema = ctx.graphql.load_introspection(raw)
    assert [bodies(schema, label) for label in ("Query.f", "Query.g", "Query.ping")] == [
        [],
        [],
        [(DEFAULT, doc("{ ping }"))],
    ]


def test_custom_scalar_literal(ctx):
    schemathesis.graphql.scalar("Money", st.just("10.5").map(nodes.String))
    schema = ctx.graphql.load_sdl("scalar Money type Query { f(m: Money!): Int }")
    assert bodies(schema, "Query.f") == [(DEFAULT, doc('{ f(m: "10.5") }'))]


def test_custom_scalar_literal_is_stable_across_runs(ctx):
    schemathesis.graphql.scalar("Money", st.integers().map(nodes.Int))
    schema = ctx.graphql.load_sdl("scalar Money type Query { f(m: Money!): Int }")
    first, second = bodies(schema, "Query.f"), bodies(schema, "Query.f")
    assert [scenario for scenario, _ in first] == [DEFAULT]
    assert first == second


def test_unknown_scalar_on_required_argument(ctx):
    schema = ctx.graphql.load_sdl("scalar FooBar type Query { f(x: FooBar!): Int }")
    with pytest.raises(UnknownScalar, match="Scalar 'FooBar' is not supported"):
        iter_cases(schema, "Query.f")


def test_default_case_metadata(ctx):
    schema = ctx.graphql.load_sdl("type Query { book(id: ID!): Int }")
    [case] = iter_cases(schema, "Query.book")
    assert (case.meta.generation.mode, case.meta.phase) == (
        GenerationMode.POSITIVE,
        PhaseInfo.coverage(DEFAULT, "Default positive test case", "Query.book", None, ParameterLocation.BODY),
    )


def test_no_positive_cases_in_negative_mode(ctx):
    schema = ctx.graphql.load_sdl("type Query { ping: String }")
    assert bodies(schema, "Query.ping", modes=(GenerationMode.NEGATIVE,)) == []
