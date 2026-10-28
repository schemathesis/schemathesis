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
MISSING = CoverageScenario.MISSING_PARAMETER
INCORRECT = CoverageScenario.INCORRECT_TYPE
ABOVE = CoverageScenario.VALUE_ABOVE_MAXIMUM
BELOW = CoverageScenario.VALUE_BELOW_MINIMUM
INVALID_ENUM = CoverageScenario.INVALID_ENUM_VALUE
UNKNOWN = CoverageScenario.UNKNOWN_ARGUMENT


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
        (
            "input R @oneOf { self: R, n: Int, s: String } input W { r: R! } type Query { f(r: R!, w: W!): Int }",
            "Query.f",
            "{ f(r: {n: 0}, w: {r: {n: 0}}) }",
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
        "one-of-input",
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
            "input C { c: C! } input W { w: C! } input O @oneOf { o: O, c: C } "
            "type Query { f(c: C!): Int g(w: W!): Int h(o: O!): Int ping: Int }",
            assume_valid=True,
        )
    )
    # A Strawberry schema elsewhere in the process makes graphql-core report `isOneOf: false`.
    next(type_ for type_ in raw["__schema"]["types"] if type_["name"] == "O")["isOneOf"] = True
    schema = ctx.graphql.load_introspection(raw)
    assert [bodies(schema, label) for label in ("Query.f", "Query.g", "Query.h", "Query.ping")] == [
        [],
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
    assert [
        case.meta.generation.mode for case in iter_cases(schema, "Query.ping", modes=(GenerationMode.NEGATIVE,))
    ] == [GenerationMode.NEGATIVE]


def negative(schema, label, *scenarios):
    return [
        (case.meta.phase.data.scenario, case.meta.phase.data.parameter, case.body)
        for case in iter_cases(schema, label, modes=(GenerationMode.NEGATIVE,))
        if not scenarios or case.meta.phase.data.scenario in scenarios
    ]


def test_missing_required_argument(ctx):
    schema = ctx.graphql.load_sdl("type Query { f(a: Int!, b: Int, c: Int! = 1): Int }")
    assert negative(schema, "Query.f", MISSING) == [(MISSING, "a", doc("{ f }"))]


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("Int!", ["null", '"a"', "true", "1.5"]),
        ("Float!", ["null", '"a"', "true"]),
        ("String!", ["null", "1", "true"]),
        ("ID!", ["null", "true", "1.5"]),
        ("Boolean!", ["null", "1", '"a"']),
        ("E!", ["null", '"a"']),
        ("I!", ["null", "1"]),
        ("[Int]", ['["a"]', "[true]", "[1.5]"]),
    ],
    ids=["int", "float", "string", "id", "boolean", "enum", "input-object", "list"],
)
def test_incorrect_type_literals(ctx, argument, expected):
    schema = ctx.graphql.load_sdl(f"enum E {{ A }} input I {{ e: E }} type Query {{ f(x: {argument}): Int }}")
    assert [body for _, parameter, body in negative(schema, "Query.f", INCORRECT) if parameter in ("x", "x.0")] == [
        doc(f"{{ f(x: {literal}) }}") for literal in expected
    ]


def test_incorrect_type_null(ctx):
    schema = ctx.graphql.load_sdl(
        "input A { zip: String! } input F { address: A } input P @oneOf { id: ID, name: String } "
        "type Query { f(a: Int!, ids: [ID!]!, filter: F, p: P!): Int }"
    )
    assert [(parameter, body) for _, parameter, body in negative(schema, "Query.f", INCORRECT) if "null" in body] == [
        ("a", doc("{ f(a: null, ids: [1], p: {id: 1}) }")),
        ("ids", doc("{ f(a: 0, ids: null, p: {id: 1}) }")),
        ("ids.0", doc("{ f(a: 0, ids: [null], p: {id: 1}) }")),
        ("filter.address.zip", doc("{ f(a: 0, ids: [1], filter: {address: {zip: null}}, p: {id: 1}) }")),
        ("p", doc("{ f(a: 0, ids: [1], p: null) }")),
        ("p.id", doc("{ f(a: 0, ids: [1], p: {id: null}) }")),
        ("p.name", doc("{ f(a: 0, ids: [1], p: {name: null}) }")),
    ]


def test_int_bounds(ctx):
    schema = ctx.graphql.load_sdl("type Query { f(n: Int!): Int }")
    assert negative(schema, "Query.f", ABOVE, BELOW) == [
        (ABOVE, "n", doc("{ f(n: 2147483648) }")),
        (BELOW, "n", doc("{ f(n: -2147483649) }")),
    ]


def test_invalid_enum_value(ctx):
    schema = ctx.graphql.load_sdl("enum E { invalid X } type Query { f(e: E!, l: [E!]): Int }")
    assert negative(schema, "Query.f", INVALID_ENUM) == [
        (INVALID_ENUM, "e", doc("{ f(e: invalid_1) }")),
        (INVALID_ENUM, "l.0", doc("{ f(e: invalid, l: [invalid_1]) }")),
    ]


def test_unknown_argument(ctx):
    schema = ctx.graphql.load_sdl("type Query { f(invalid: Int!): Int g: Int }")
    assert [negative(schema, label, UNKNOWN) for label in ("Query.f", "Query.g")] == [
        [(UNKNOWN, "invalid_1", doc("{ f(invalid: 0, invalid_1: 0) }"))],
        [(UNKNOWN, "invalid", doc("{ g(invalid: 0) }"))],
    ]


def test_custom_scalar_negative_cases(ctx):
    schemathesis.graphql.scalar("Money", st.just("10.5").map(nodes.String))
    schema = ctx.graphql.load_sdl("scalar Money type Query { f(m: Money!, l: [Money!]!): Int }")
    assert negative(schema, "Query.f") == [
        (MISSING, "m", doc('{ f(l: ["10.5"]) }')),
        (INCORRECT, "m", doc('{ f(m: null, l: ["10.5"]) }')),
        (MISSING, "l", doc('{ f(m: "10.5") }')),
        (INCORRECT, "l", doc('{ f(m: "10.5", l: null) }')),
        (INCORRECT, "l.0", doc('{ f(m: "10.5", l: [null]) }')),
        (UNKNOWN, "invalid", doc('{ f(m: "10.5", l: ["10.5"], invalid: 0) }')),
    ]


def test_negative_cases_skip_unknown_scalars_outside_required_arguments(ctx):
    schema = ctx.graphql.load_sdl("scalar FooBar input F { x: FooBar! y: Int! } type Query { f(a: F, n: Int!): Int }")
    assert [(scenario, parameter) for scenario, parameter, _ in negative(schema, "Query.f")] == [
        (INCORRECT, "a"),
        (INCORRECT, "a.x"),
        (MISSING, "n"),
        (INCORRECT, "n"),
        (INCORRECT, "n"),
        (INCORRECT, "n"),
        (INCORRECT, "n"),
        (ABOVE, "n"),
        (BELOW, "n"),
        (UNKNOWN, "invalid"),
    ]


def test_negative_cases_skip_elements_without_a_finite_value(ctx):
    raw = graphql.introspection_from_schema(
        graphql.build_schema(
            "input C { c: C! } input G { c: C! x: Int } input H { g: G } type Query { f(h: H, l: [G]): Int }",
            assume_valid=True,
        )
    )
    schema = ctx.graphql.load_introspection(raw)
    assert [(scenario, parameter) for scenario, parameter, _ in negative(schema, "Query.f")] == [
        (INCORRECT, "h"),
        (INCORRECT, "h.g"),
        (INCORRECT, "h.g.c"),
        (INCORRECT, "h.g.c"),
        (INCORRECT, "h.g.c.c"),
        (INCORRECT, "h.g.c.c"),
        (INCORRECT, "l.0"),
        (INCORRECT, "l.0.c"),
        (INCORRECT, "l.0.c"),
        (INCORRECT, "l.0.c.c"),
        (INCORRECT, "l.0.c.c"),
        (UNKNOWN, "invalid"),
    ]


def test_negative_case_metadata(ctx):
    schema = ctx.graphql.load_sdl(
        "input Address { zip: String! } input Filter { address: Address } type Query { users(filter: Filter!): Int }"
    )
    assert [
        (case.meta.generation.mode, case.meta.phase.data.location, case.meta.phase.data.description)
        for case in iter_cases(schema, "Query.users", modes=(GenerationMode.NEGATIVE,))
        if case.meta.phase.data.parameter in ("filter", "filter.address.zip")
    ] == [
        (GenerationMode.NEGATIVE, "Query.users(filter:)", "Missing required argument `filter`"),
        (GenerationMode.NEGATIVE, "Query.users(filter:)", "Incorrect type for `filter`"),
        (GenerationMode.NEGATIVE, "Query.users(filter:)", "Incorrect type for `filter`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
    ]


@pytest.mark.parametrize(
    "sdl",
    [
        "enum E { A B } input A { zip: String! n: Int f: Float } input F { address: A e: E ids: [ID!] } "
        "input P @oneOf { id: ID, name: String, f: F } "
        'type Query { f(a: Int!, b: Boolean, s: String! = "x", ids: [ID!]!, filter: F, p: P!, e: [E]): Int }',
        "scalar Money type Query { f(m: Money!, l: [Money!]!, o: [[Money]]): Int }",
    ],
    ids=["builtin", "custom-scalar"],
)
def test_negative_cases_are_invalid(ctx, sdl):
    schemathesis.graphql.scalar("Money", st.just("10.5").map(nodes.String))
    target = graphql.build_schema(sdl)
    cases = negative(ctx.graphql.load_sdl(sdl), "Query.f")
    assert cases
    assert [
        (parameter, body) for _, parameter, body in cases if not graphql.validate(target, graphql.parse(body))
    ] == []
