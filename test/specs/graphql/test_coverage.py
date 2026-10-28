import graphql
import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.failures import FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CoverageScenario, PhaseInfo
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS, UnknownScalar
from schemathesis.specs.openapi.checks import negative_data_rejection, positive_data_acceptance

DEFAULT = CoverageScenario.DEFAULT_POSITIVE_TEST
MISSING = CoverageScenario.MISSING_PARAMETER
INCORRECT = CoverageScenario.INCORRECT_TYPE
ABOVE = CoverageScenario.VALUE_ABOVE_MAXIMUM
BELOW = CoverageScenario.VALUE_BELOW_MINIMUM
INVALID_ENUM = CoverageScenario.INVALID_ENUM_VALUE
UNKNOWN = CoverageScenario.UNKNOWN_ARGUMENT
MISSING_FIELD = CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
UNEXPECTED_FIELD = CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES
OBJECTS_SCHEMA = (
    "input A { zip: String! invalid: Int } input F { a: A! n: Int! } input P @oneOf { id: ID, name: String } "
    "type Query { f(filter: F, p: P!): Int }"
)
RECURSIVE_SCHEMA = (
    "input F { and: [F!], x: Int, req: String! } input B { _not: B, _and: [B!], id: Int } "
    "type Query { f(f: F!, b: B): Int }"
)
ORACLE_SCHEMAS = [
    "enum E { A B } input A { zip: String! n: Int f: Float } input F { address: A e: E ids: [ID!] } "
    "input P @oneOf { id: ID, name: String, f: F } "
    'type Query { f(a: Int!, b: Boolean, s: String! = "x", ids: [ID!]!, filter: F, p: P!, e: [E]): Int }',
    "scalar Money input M { m: Money! } type Query { f(m: Money!, l: [Money!]!, o: [[Money]], i: M!): Int }",
    OBJECTS_SCHEMA,
    RECURSIVE_SCHEMA,
]
ORACLE_IDS = ["builtin", "custom-scalar", "objects", "recursive"]


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
        (MISSING_FIELD, "a.x"),
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
        (UNEXPECTED_FIELD, "h"),
        (INCORRECT, "h.g"),
        (MISSING_FIELD, "h.g.c"),
        (INCORRECT, "h.g.c"),
        (INCORRECT, "h.g.c"),
        (INCORRECT, "l.0"),
        (MISSING_FIELD, "l.0.c"),
        (INCORRECT, "l.0.c"),
        (INCORRECT, "l.0.c"),
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
        (GenerationMode.NEGATIVE, "Query.users(filter:)", "Unexpected field in `filter`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Missing required field `filter.address.zip`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
        (GenerationMode.NEGATIVE, "Address.zip", "Incorrect type for `filter.address.zip`"),
    ]


def test_object_missing_required_property(ctx):
    schema = ctx.graphql.load_sdl(OBJECTS_SCHEMA)
    assert negative(schema, "Query.f", MISSING_FIELD) == [
        (MISSING_FIELD, "filter.a", doc("{ f(filter: {n: 0}, p: {id: 1}) }")),
        (MISSING_FIELD, "filter.a.zip", doc("{ f(filter: {a: {}, n: 0}, p: {id: 1}) }")),
        (MISSING_FIELD, "filter.n", doc('{ f(filter: {a: {zip: ""}}, p: {id: 1}) }')),
        (MISSING_FIELD, "p", doc("{ f(p: {}) }")),
    ]


def test_object_unexpected_properties(ctx):
    schema = ctx.graphql.load_sdl(OBJECTS_SCHEMA)
    assert negative(schema, "Query.f", UNEXPECTED_FIELD) == [
        (UNEXPECTED_FIELD, "filter", doc('{ f(filter: {a: {zip: ""}, n: 0, invalid: 0}, p: {id: 1}) }')),
        (UNEXPECTED_FIELD, "filter.a", doc('{ f(filter: {a: {zip: "", invalid_1: 0}, n: 0}, p: {id: 1}) }')),
        (UNEXPECTED_FIELD, "p", doc("{ f(p: {invalid: 0}) }")),
        (UNEXPECTED_FIELD, "p", doc('{ f(p: {id: 1, name: ""}) }')),
    ]


def test_object_case_metadata(ctx):
    schema = ctx.graphql.load_sdl(OBJECTS_SCHEMA)
    assert [
        (case.meta.phase.data.location, case.meta.phase.data.description)
        for case in iter_cases(schema, "Query.f", modes=(GenerationMode.NEGATIVE,))
        if case.meta.phase.data.scenario in (MISSING_FIELD, UNEXPECTED_FIELD)
    ] == [
        ("Query.f(filter:)", "Unexpected field in `filter`"),
        ("F.a", "Missing required field `filter.a`"),
        ("F.a", "Unexpected field in `filter.a`"),
        ("A.zip", "Missing required field `filter.a.zip`"),
        ("F.n", "Missing required field `filter.n`"),
        ("Query.f(p:)", "Missing required field `p`"),
        ("Query.f(p:)", "Unexpected field in `p`"),
        ("Query.f(p:)", "Unexpected field in `p`"),
    ]


def test_one_of_fields_without_a_value_are_skipped(ctx):
    schema = ctx.graphql.load_sdl("scalar FooBar input P @oneOf { m: FooBar, id: ID } type Query { f(p: P!): Int }")
    assert [bodies(schema, "Query.f"), negative(schema, "Query.f", UNEXPECTED_FIELD)] == [
        [(DEFAULT, doc("{ f(p: {id: 1}) }"))],
        [(UNEXPECTED_FIELD, "p", doc("{ f(p: {invalid: 0}) }"))],
    ]


def test_recursive_input_negative_cases(ctx):
    schema = ctx.graphql.load_sdl(RECURSIVE_SCHEMA)
    cases = negative(schema, "Query.f")
    assert [list(dict.fromkeys(parameter for _, parameter, _ in cases)), [b for _, _, b in cases if "_not" in b]] == [
        ["f", "f.x", "f.req", "b", "b.id", "invalid"],
        [],
    ]


@pytest.mark.parametrize("sdl", ORACLE_SCHEMAS, ids=ORACLE_IDS)
def test_negative_cases_are_invalid(ctx, sdl):
    schemathesis.graphql.scalar("Money", st.just('a"b\\c é').map(nodes.String))
    target = graphql.build_schema(sdl)
    cases = negative(ctx.graphql.load_sdl(sdl), "Query.f")
    assert cases
    assert [
        (parameter, body) for _, parameter, body in cases if not graphql.validate(target, graphql.parse(body))
    ] == []


@pytest.mark.parametrize("sdl", ORACLE_SCHEMAS, ids=ORACLE_IDS)
def test_positive_cases_are_valid(ctx, sdl):
    schemathesis.graphql.scalar("Money", st.just('a"b\\c é').map(nodes.String))
    target = graphql.build_schema(sdl)
    bodies_ = [case.body for case in iter_cases(ctx.graphql.load_sdl(sdl), "Query.f")]
    assert bodies_
    assert [body for body in bodies_ if graphql.validate(target, graphql.parse(body))] == []


def run_against(server):
    schema = schemathesis.graphql.from_url(server.schema_url)
    results = []
    for result in schema.get_all_operations():
        operation = result.ok()
        for case in schema.iter_coverage_cases(
            operation, generation_modes=list(GenerationMode), generation_config=schema.config.generation
        ):
            data = case.meta.phase.data
            try:
                case.call_and_validate(checks=[negative_data_rejection, positive_data_acceptance])
                failure = None
            except FailureGroup as exc:
                failure = tuple(type(inner).__name__ for inner in exc.exceptions)
            results.append((case.meta.generation.mode, data.scenario, data.parameter, failure))
    return results


def test_conforming_server_has_no_false_positives(ctx):
    results = run_against(ctx.graphql.apps.coverage_inputs())
    assert len(results) > 1
    assert [result for result in results if result[3] is not None] == []


def test_lenient_server_fails_every_negative_scenario(ctx):
    results = run_against(ctx.graphql.apps.lenient())
    assert [result for result in results if (result[3] is not None) != (result[0] == GenerationMode.NEGATIVE)] == []
    assert {scenario for mode, scenario, _, _ in results if mode == GenerationMode.NEGATIVE} == {
        MISSING,
        INCORRECT,
        ABOVE,
        BELOW,
        INVALID_ENUM,
        MISSING_FIELD,
        UNEXPECTED_FIELD,
        UNKNOWN,
    }
    assert {failure for _, _, _, failure in results} == {None, ("AcceptedNegativeData",)}
