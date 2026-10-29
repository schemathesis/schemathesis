import contextlib

import graphql
import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.failures import FailureGroup
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.coverage import GenerationSession
from schemathesis.generation.meta import CoverageScenario, PhaseInfo
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS, UnknownScalar
from schemathesis.specs.openapi.checks import (
    negative_data_rejection,
    positive_data_acceptance,
    response_schema_conformance,
)

DEFAULT = CoverageScenario.DEFAULT_POSITIVE_TEST
MISSING = CoverageScenario.MISSING_PARAMETER
INCORRECT = CoverageScenario.INCORRECT_TYPE
ABOVE = CoverageScenario.VALUE_ABOVE_MAXIMUM
BELOW = CoverageScenario.VALUE_BELOW_MINIMUM
INVALID_ENUM = CoverageScenario.INVALID_ENUM_VALUE
UNKNOWN = CoverageScenario.UNKNOWN_ARGUMENT
MISSING_FIELD = CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
UNEXPECTED_FIELD = CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES
ENUM = CoverageScenario.ENUM_VALUE
ONLY_REQUIRED = CoverageScenario.OBJECT_ONLY_REQUIRED
ALL_FIELDS = CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL
NULL = CoverageScenario.NULL_VALUE
MINIMUM = CoverageScenario.MINIMUM_VALUE
MAXIMUM = CoverageScenario.MAXIMUM_VALUE
EMPTY_STRING = CoverageScenario.MINIMUM_LENGTH_STRING
VALID_NUMBER = CoverageScenario.VALID_NUMBER
VALID_STRING = CoverageScenario.VALID_STRING
VALID_BOOLEAN = CoverageScenario.VALID_BOOLEAN
VALID_ARRAY = CoverageScenario.VALID_ARRAY
SELECT = CoverageScenario.FIELD_SELECTION
VALID_KINDS = (VALID_NUMBER, VALID_STRING, VALID_BOOLEAN, VALID_ARRAY, CoverageScenario.VALID_OBJECT)
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
    "scalar Money interface Named { name: String item: Item } "
    "type Author implements Named { name: String! item: Item born: Int books(first: Int!): [Book] } "
    "type Book { title: String author: Author price(currency: Money!): Int } union Item = Author | Book "
    "type Query { f(id: ID!): Named }",
]
ORACLE_IDS = ["builtin", "custom-scalar", "objects", "recursive", "selection"]


@pytest.fixture(autouse=True)
def clear_custom_scalars():
    yield
    CUSTOM_SCALARS.clear()


def register_scalars():
    schemathesis.graphql.scalar("Money", st.just("10.5").map(nodes.String))
    schemathesis.graphql.scalar("Count", st.just(5).map(nodes.Int))


def iter_cases(schema, label, *, modes=(GenerationMode.POSITIVE,), session=None):
    return list(
        schema.iter_coverage_cases(
            schema.find_operation_by_label(label),
            generation_modes=list(modes),
            generation_config=schema.config.generation,
            session=session,
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
        ("scalar Money type Query { f(m: Money!): Int }", "Query.f", '{ f(m: "10.5") }'),
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
        "custom-scalar",
    ],
)
def test_default_case(ctx, sdl, label, expected):
    register_scalars()
    schema = ctx.graphql.load_sdl(sdl)
    assert positive(schema, label, DEFAULT) == [(DEFAULT, None, doc(expected))]


def test_no_cases_for_required_input_cycle(ctx):
    # Introspection results are not validated, so a cycle the SDL loader would reject can still arrive.
    raw = graphql.introspection_from_schema(
        graphql.build_schema(
            "input C { c: C! } input W { w: C! } input O @oneOf { o: O, c: C } "
            "type Box { id: ID take(c: C!): Box } "
            "type Query { f(c: C!): Int g(w: W!): Int h(o: O!): Int ping: Int box: Box }",
            assume_valid=True,
        )
    )
    # Another schema library in the process can make graphql-core report `isOneOf: false`.
    next(type_ for type_ in raw["__schema"]["types"] if type_["name"] == "O")["isOneOf"] = True
    schema = ctx.graphql.load_introspection(raw)
    assert [bodies(schema, label) for label in ("Query.f", "Query.g", "Query.h", "Query.ping", "Query.box")] == [
        [],
        [],
        [],
        [(DEFAULT, doc("{ ping }"))],
        [(DEFAULT, doc("{ box { id } }"))],
    ]


def test_custom_scalar_literal_is_stable_across_runs(ctx):
    schemathesis.graphql.scalar("Money", st.integers().map(nodes.Int))
    schema = ctx.graphql.load_sdl("scalar Money type Query { f(m: Money!): Int }")
    first, second = bodies(schema, "Query.f"), bodies(schema, "Query.f")
    assert [scenario for scenario, _ in first] == [DEFAULT]
    assert first == second


def sent_before_unknown_scalar(schema, modes=(GenerationMode.POSITIVE, GenerationMode.NEGATIVE)):
    sent = []
    with pytest.raises(UnknownScalar, match="Scalar 'FooBar' is not supported"):
        for case in schema.iter_coverage_cases(
            schema.find_operation_by_label("Query.f"),
            generation_modes=list(modes),
            generation_config=schema.config.generation,
        ):
            sent.append((case.meta.phase.data.scenario, case.meta.phase.data.parameter, case.body))
    return sent


# Leaving out or nulling a required argument fails validation whatever literals its scalar accepts.
def test_unknown_scalar_on_required_argument_negative_cases(ctx):
    schema = ctx.graphql.load_sdl(
        "scalar FooBar input F { x: FooBar! } type Query { f(x: FooBar!, a: Int!, b: F!, c: FooBar): Int }"
    )
    assert sent_before_unknown_scalar(schema) == [
        (MISSING, "x", doc("{ f(a: 0) }")),
        (INCORRECT, "x", doc("{ f(x: null, a: 0) }")),
        (INCORRECT, "b", doc("{ f(a: 0, b: null) }")),
        (INCORRECT, "b", doc("{ f(a: 0, b: 1) }")),
    ]


def test_unknown_scalar_with_required_input_cycle(ctx):
    raw = graphql.introspection_from_schema(
        graphql.build_schema(
            "scalar FooBar input C { c: C! } type Query { f(x: FooBar!, c: C!): Int }", assume_valid=True
        )
    )
    assert sent_before_unknown_scalar(ctx.graphql.load_introspection(raw)) == []


def test_unknown_scalar_on_required_argument_in_positive_mode(ctx):
    schema = ctx.graphql.load_sdl("scalar FooBar type Query { f(x: FooBar!): Int }")
    assert sent_before_unknown_scalar(schema, modes=(GenerationMode.POSITIVE,)) == []


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


def select(schema, label, mode, scenarios):
    return [
        (case.meta.phase.data.scenario, case.meta.phase.data.parameter, case.body)
        for case in iter_cases(schema, label, modes=(mode,))
        if not scenarios or case.meta.phase.data.scenario in scenarios
    ]


def negative(schema, label, *scenarios):
    return select(schema, label, GenerationMode.NEGATIVE, scenarios)


def positive(schema, label, *scenarios):
    return select(schema, label, GenerationMode.POSITIVE, scenarios)


@pytest.mark.parametrize(
    ("sdl", "scenarios", "expected"),
    [
        pytest.param(
            "type Query { f(a: Int!, b: Int, c: Int! = 1): Int }",
            [MISSING],
            [(MISSING, "a", "{ f }")],
            id="missing-required-argument",
        ),
        pytest.param(
            "type Query { f(n: Int!): Int }",
            [ABOVE, BELOW],
            [(ABOVE, "n", "{ f(n: 2147483648) }"), (BELOW, "n", "{ f(n: -2147483649) }")],
            id="int-bounds",
        ),
        pytest.param(
            "enum E { invalid X } type Query { f(e: E!, l: [E!]): Int }",
            [INVALID_ENUM],
            [(INVALID_ENUM, "e", "{ f(e: invalid_1) }"), (INVALID_ENUM, "l.0", "{ f(e: invalid, l: [invalid_1]) }")],
            id="invalid-enum-value",
        ),
        pytest.param(
            "type Query { f(invalid: Int!): Int }",
            [UNKNOWN],
            [(UNKNOWN, "invalid_1", "{ f(invalid: 0, invalid_1: 0) }")],
            id="unknown-argument-name-taken",
        ),
        pytest.param(
            "type Query { f: Int }",
            [UNKNOWN],
            [(UNKNOWN, "invalid", "{ f(invalid: 0) }")],
            id="unknown-argument-without-arguments",
        ),
        pytest.param(
            "scalar Money type Query { f(m: Money!, l: [Money!]!): Int }",
            [],
            [
                (MISSING, "m", '{ f(l: ["10.5"]) }'),
                (INCORRECT, "m", '{ f(m: null, l: ["10.5"]) }'),
                (MISSING, "l", '{ f(m: "10.5") }'),
                (INCORRECT, "l", '{ f(m: "10.5", l: null) }'),
                (INCORRECT, "l.0", '{ f(m: "10.5", l: [null]) }'),
                (UNKNOWN, "invalid", '{ f(m: "10.5", l: ["10.5"], invalid: 0) }'),
            ],
            id="custom-scalar",
        ),
        pytest.param(
            OBJECTS_SCHEMA,
            [MISSING_FIELD],
            [
                (MISSING_FIELD, "filter.a", "{ f(filter: {n: 0}, p: {id: 1}) }"),
                (MISSING_FIELD, "filter.a.zip", "{ f(filter: {a: {}, n: 0}, p: {id: 1}) }"),
                (MISSING_FIELD, "filter.n", '{ f(filter: {a: {zip: ""}}, p: {id: 1}) }'),
                (MISSING_FIELD, "p", "{ f(p: {}) }"),
            ],
            id="object-missing-required-property",
        ),
        pytest.param(
            OBJECTS_SCHEMA,
            [UNEXPECTED_FIELD],
            [
                (UNEXPECTED_FIELD, "filter", '{ f(filter: {a: {zip: ""}, n: 0, invalid: 0}, p: {id: 1}) }'),
                (UNEXPECTED_FIELD, "filter.a", '{ f(filter: {a: {zip: "", invalid_1: 0}, n: 0}, p: {id: 1}) }'),
                (UNEXPECTED_FIELD, "p", "{ f(p: {invalid: 0}) }"),
                (UNEXPECTED_FIELD, "p", '{ f(p: {id: 1, name: ""}) }'),
            ],
            id="object-unexpected-properties",
        ),
    ],
)
def test_negative_cases(ctx, sdl, scenarios, expected):
    register_scalars()
    schema = ctx.graphql.load_sdl(sdl)
    assert negative(schema, "Query.f", *scenarios) == [
        (scenario, parameter, doc(body)) for scenario, parameter, body in expected
    ]


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


def test_cases_skip_elements_without_a_finite_value(ctx):
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
        (UNKNOWN, "invalid"),
    ]
    assert positive(schema, "Query.f") == [
        (DEFAULT, None, doc("{ f }")),
        (NULL, "h", doc("{ f(h: null) }")),
        (ONLY_REQUIRED, "h", doc("{ f(h: {}) }")),
        (NULL, "h.g", doc("{ f(h: {g: null}) }")),
        (NULL, "l", doc("{ f(l: null) }")),
        (VALID_ARRAY, "l", doc("{ f(l: []) }")),
        (NULL, "l.0", doc("{ f(l: [null]) }")),
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


def walked_parameters(schema):
    return list(dict.fromkeys(parameter for _, parameter, _ in positive(schema, "Query.f") if parameter is not None))


def test_input_walk_stops_at_depth_three(ctx):
    # Filters that link to each other's filters have exponentially many paths.
    schema = ctx.graphql.load_sdl(
        "input T0 { t0: T0 t1: T1 t2: T2 t3: T3 x: Int } input T1 { t0: T0 t1: T1 t2: T2 t3: T3 x: Int } "
        "input T2 { t0: T0 t1: T1 t2: T2 t3: T3 x: Int } input T3 { t0: T0 t1: T1 t2: T2 t3: T3 x: Int } "
        "type Query { f(a: T0): Int }"
    )
    assert walked_parameters(schema) == [
        "a",
        "a.t1",
        "a.t1.t2",
        "a.t1.t3",
        "a.t1.x",
        "a.t2",
        "a.t2.t1",
        "a.t2.t3",
        "a.t2.x",
        "a.t3",
        "a.t3.t1",
        "a.t3.t2",
        "a.t3.x",
        "a.x",
    ]


def test_input_type_fields_are_walked_once_per_argument(ctx):
    schema = ctx.graphql.load_sdl("input P { lo: Int } input R { a: P b: P } type Query { f(r: R, s: P): Int }")
    assert walked_parameters(schema) == ["r", "r.a", "r.a.lo", "r.b", "s", "s.lo"]


@pytest.mark.parametrize(
    ("sdl", "scenarios", "expected"),
    [
        pytest.param(
            "enum E { A B } input I { e: E! } type Query { f(e: E, i: I!): Int }",
            [ENUM],
            [
                (ENUM, "e", "{ f(e: A, i: {e: A}) }"),
                (ENUM, "e", "{ f(e: B, i: {e: A}) }"),
                (ENUM, "i.e", "{ f(i: {e: B}) }"),
            ],
            id="enum-values",
        ),
        pytest.param(
            "input A { zip: String! n: Int } input F { a: A! n: Int k: F } input P @oneOf { id: ID, name: String } "
            "type Query { f(filter: F, p: P!): Int }",
            [],
            [
                (DEFAULT, None, "{ f(p: {id: 1}) }"),
                (NULL, "filter", "{ f(filter: null, p: {id: 1}) }"),
                (ONLY_REQUIRED, "filter", '{ f(filter: {a: {zip: ""}}, p: {id: 1}) }'),
                (ALL_FIELDS, "filter", '{ f(filter: {a: {zip: ""}, n: 0}, p: {id: 1}) }'),
                (ALL_FIELDS, "filter.a", '{ f(filter: {a: {zip: "", n: 0}}, p: {id: 1}) }'),
                (NULL, "filter.a.n", '{ f(filter: {a: {zip: "", n: null}}, p: {id: 1}) }'),
                (MINIMUM, "filter.a.n", '{ f(filter: {a: {zip: "", n: -2147483648}}, p: {id: 1}) }'),
                (MAXIMUM, "filter.a.n", '{ f(filter: {a: {zip: "", n: 2147483647}}, p: {id: 1}) }'),
                (NULL, "filter.n", '{ f(filter: {a: {zip: ""}, n: null}, p: {id: 1}) }'),
                (MINIMUM, "filter.n", '{ f(filter: {a: {zip: ""}, n: -2147483648}, p: {id: 1}) }'),
                (MAXIMUM, "filter.n", '{ f(filter: {a: {zip: ""}, n: 2147483647}, p: {id: 1}) }'),
                (EMPTY_STRING, "p.name", '{ f(p: {name: ""}) }'),
            ],
            id="input-objects",
        ),
        pytest.param(
            "scalar Money scalar Count enum E { A } input I { x: Boolean } type Query { "
            "f(i: Int, fl: Float, s: String, id: ID, b: Boolean, o: I, l: [Int], m: Money, c: Count, e: E): Int }",
            VALID_KINDS,
            [
                (VALID_NUMBER, "i", "{ f(i: 0) }"),
                (VALID_NUMBER, "fl", "{ f(fl: 0) }"),
                (VALID_STRING, "id", "{ f(id: 1) }"),
                (VALID_BOOLEAN, "b", "{ f(b: true) }"),
                (VALID_ARRAY, "l", "{ f(l: []) }"),
                (VALID_ARRAY, "l", "{ f(l: [0]) }"),
                (VALID_STRING, "m", '{ f(m: "10.5") }'),
                (VALID_NUMBER, "c", "{ f(c: 5) }"),
            ],
            id="valid-literal-kinds",
        ),
        pytest.param(
            "input P @oneOf { id: ID, n: Int } input I { x: Int, y: Int! } "
            "type Query { f(a: Int, b: Int! = 10, c: Int!, i: I, p: P, l: [Int]): Int }",
            [NULL],
            [
                (NULL, "a", "{ f(a: null, c: 0) }"),
                (NULL, "i", "{ f(c: 0, i: null) }"),
                (NULL, "i.x", "{ f(c: 0, i: {x: null, y: 0}) }"),
                (NULL, "p", "{ f(c: 0, p: null) }"),
                (NULL, "l", "{ f(c: 0, l: null) }"),
                (NULL, "l.0", "{ f(c: 0, l: [null]) }"),
            ],
            id="null-values",
        ),
        pytest.param(
            "type Query { f(n: Int!): Int }",
            [MINIMUM, MAXIMUM],
            [(MINIMUM, "n", "{ f(n: -2147483648) }"), (MAXIMUM, "n", "{ f(n: 2147483647) }")],
            id="int-boundaries",
        ),
        pytest.param(
            "type Query { f(s: String, id: ID): Int }",
            [EMPTY_STRING],
            [(EMPTY_STRING, "s", '{ f(s: "") }')],
            id="empty-string-not-for-id",
        ),
        pytest.param(
            "type Query { f(l: [Int!]): Int }",
            [VALID_ARRAY],
            [(VALID_ARRAY, "l", "{ f(l: []) }"), (VALID_ARRAY, "l", "{ f(l: [0]) }")],
            id="array-sizes",
        ),
        pytest.param(
            "type Query { f(s: String, r: String!): Int }",
            [],
            [
                (DEFAULT, None, '{ f(r: "") }'),
                (NULL, "s", '{ f(s: null, r: "") }'),
                (EMPTY_STRING, "s", '{ f(s: "", r: "") }'),
            ],
            id="duplicate-bodies-keep-specific-label",
        ),
        pytest.param(
            "scalar FooBar input F { x: FooBar! y: Boolean! } type Query { f(a: F, u: FooBar, n: Boolean!): Int }",
            [],
            [
                (DEFAULT, None, "{ f(n: true) }"),
                (NULL, "a", "{ f(a: null, n: true) }"),
                (NULL, "u", "{ f(u: null, n: true) }"),
            ],
            id="unknown-scalars-get-only-null",
        ),
    ],
)
def test_positive_cases(ctx, sdl, scenarios, expected):
    register_scalars()
    schema = ctx.graphql.load_sdl(sdl)
    assert positive(schema, "Query.f", *scenarios) == [
        (scenario, parameter, doc(body)) for scenario, parameter, body in expected
    ]


def test_positive_case_metadata(ctx):
    schema = ctx.graphql.load_sdl(
        "enum E { A } input I { x: Int } type Query { f(n: Int, s: String, e: E, i: I, l: [ID]): Int }"
    )
    assert [
        (
            case.meta.generation.mode,
            case.meta.phase.data.scenario,
            case.meta.phase.data.location,
            case.meta.phase.data.description,
        )
        for case in iter_cases(schema, "Query.f")
    ] == [
        (GenerationMode.POSITIVE, DEFAULT, "Query.f", "Default positive test case"),
        (GenerationMode.POSITIVE, NULL, "Query.f(n:)", "Explicit null for `n`"),
        (GenerationMode.POSITIVE, MINIMUM, "Query.f(n:)", "Minimum value for `n`"),
        (GenerationMode.POSITIVE, MAXIMUM, "Query.f(n:)", "Maximum value for `n`"),
        (GenerationMode.POSITIVE, VALID_NUMBER, "Query.f(n:)", "Valid value for `n`"),
        (GenerationMode.POSITIVE, NULL, "Query.f(s:)", "Explicit null for `s`"),
        (GenerationMode.POSITIVE, EMPTY_STRING, "Query.f(s:)", "Empty string for `s`"),
        (GenerationMode.POSITIVE, NULL, "Query.f(e:)", "Explicit null for `e`"),
        (GenerationMode.POSITIVE, ENUM, "Query.f(e:)", "Enum value for `e`"),
        (GenerationMode.POSITIVE, NULL, "Query.f(i:)", "Explicit null for `i`"),
        (GenerationMode.POSITIVE, ONLY_REQUIRED, "Query.f(i:)", "Required fields of `i`"),
        (GenerationMode.POSITIVE, ALL_FIELDS, "Query.f(i:)", "All fields of `i`"),
        (GenerationMode.POSITIVE, NULL, "I.x", "Explicit null for `i.x`"),
        (GenerationMode.POSITIVE, MINIMUM, "I.x", "Minimum value for `i.x`"),
        (GenerationMode.POSITIVE, MAXIMUM, "I.x", "Maximum value for `i.x`"),
        (GenerationMode.POSITIVE, NULL, "Query.f(l:)", "Explicit null for `l`"),
        (GenerationMode.POSITIVE, VALID_ARRAY, "Query.f(l:)", "Empty list for `l`"),
        (GenerationMode.POSITIVE, VALID_ARRAY, "Query.f(l:)", "Valid value for `l`"),
        (GenerationMode.POSITIVE, NULL, "Query.f(l:)", "Explicit null for `l.0`"),
    ]


def selections(schema, label):
    return [
        (case.meta.phase.data.location, case.meta.phase.data.description, case.body)
        for case in iter_cases(schema, label)
        if case.meta.phase.data.scenario == SELECT
    ]


@pytest.mark.parametrize(
    ("sdl", "label", "expected"),
    [
        pytest.param(
            "type User { id: ID! name: String posts: [Post] friends: [User] } "
            "type Post { title: String author: User comments: [Comment] } "
            "type Comment { text: String post: Post reply: Comment like: Like } "
            "type Like { by: String extra: Extra } type Extra { x: Int } type Query { user: User }",
            "Query.user",
            [
                ("User.posts", "{ user { id name posts { title } } }"),
                ("User.friends", "{ user { id name friends { id name } } }"),
                ("Post.author", "{ user { id name posts { author { id name } } } }"),
                ("Post.comments", "{ user { id name posts { comments { text } } } }"),
                ("Comment.post", "{ user { id name posts { comments { post { title } } } } }"),
                ("Comment.reply", "{ user { id name posts { comments { reply { text } } } } }"),
                ("Comment.like", "{ user { id name posts { comments { like { by } } } } }"),
            ],
            id="depth-three",
        ),
        pytest.param(
            "scalar FooBar type Post { title: String } "
            "type User { id: ID! posts(first: Int!): [Post] avatar(size: Int!): String odd(x: FooBar!): String } "
            "type Query { user(id: ID!): User }",
            "Query.user",
            [
                ("User.posts", "{ user(id: 1) { id posts(first: 0) { title } } }"),
                ("User.avatar", "{ user(id: 1) { id avatar(size: 0) } }"),
            ],
            id="required-arguments",
        ),
        pytest.param(
            "interface Named { name: String } type Author implements Named { name: String! born: Int } "
            "type Ghost implements Named { name: String } type Query { named: Named }",
            "Query.named",
            [
                ("Author", "{ named { name ... on Author { born } } }"),
                ("Ghost", "{ named { name ... on Ghost { __typename } } }"),
            ],
            id="interface",
        ),
        pytest.param(
            "interface Named { name: String item: Item } "
            "type Author implements Named { name: String! item: Item born: Int books(first: Int!): [Book] } "
            "type Book { title: String author: Author } union Item = Author | Book type Query { f(id: ID!): Named }",
            "Query.f",
            [
                ("Named.item", "{ f(id: 1) { name item { __typename } } }"),
                ("Author", "{ f(id: 1) { name ... on Author { born } } }"),
                ("Book", "{ f(id: 1) { name item { ... on Book { title } } } }"),
                ("Author.books", "{ f(id: 1) { name ... on Author { books(first: 0) { title } } } }"),
                ("Book.author", "{ f(id: 1) { name item { ... on Book { author { name born } } } } }"),
            ],
            id="union-member-reached-earlier",
        ),
        pytest.param(
            "type A { u: U } union U = C type C { d: D } type D { e: E } type E { x: Int } type Query { a: A }",
            "Query.a",
            [
                ("A.u", "{ a { __typename u { __typename } } }"),
                ("C", "{ a { __typename u { ... on C { __typename } } } }"),
                ("C.d", "{ a { __typename u { ... on C { d { __typename } } } } }"),
                ("D.e", "{ a { __typename u { ... on C { d { e { x } } } } } }"),
            ],
            id="fragment-adds-no-depth",
        ),
        pytest.param(
            "type Book { title: String } type Shelf { books: [Book] } type Empty { shelf: Shelf } "
            "union Item = Book | Empty type Query { item: Item }",
            "Query.item",
            [
                ("Book", "{ item { __typename ... on Book { title } } }"),
                ("Empty", "{ item { __typename ... on Empty { __typename } } }"),
                ("Empty.shelf", "{ item { __typename ... on Empty { shelf { __typename } } } }"),
                ("Shelf.books", "{ item { __typename ... on Empty { shelf { books { title } } } } }"),
            ],
            id="union",
        ),
        pytest.param("type Query { f(n: Int!): [String] }", "Query.f", [], id="leaf-return"),
    ],
)
def test_field_selection(ctx, sdl, label, expected):
    schema = ctx.graphql.load_sdl(sdl)
    assert selections(schema, label) == [(location, f"Select `{location}`", doc(body)) for location, body in expected]


SHARED_TYPES_SCHEMA = (
    "input Address { zip: String! } type Author { name: String } type Book { id: ID title: String author: Author } "
    "type Query { a(address: Address): Book b(address: Address): Book }"
)


def case_summary(cases):
    return [(case.meta.phase.data.scenario, case.meta.phase.data.parameter, case.body) for case in cases]


# Nested input fields fail validation and nested fields resolve the same way whichever operation reaches them.
@pytest.mark.parametrize("session", [None, GenerationSession()], ids=["without-session", "with-session"])
def test_cases_shared_by_operations_run_once(ctx, session):
    schema = ctx.graphql.load_sdl(SHARED_TYPES_SCHEMA)
    modes = list(GenerationMode)
    first = case_summary(iter_cases(schema, "Query.a", modes=modes, session=session))
    second = case_summary(iter_cases(schema, "Query.b", modes=modes, session=session))
    assert [(scenario, parameter) for scenario, parameter, _ in first] == [
        (DEFAULT, None),
        (NULL, "address"),
        (ONLY_REQUIRED, "address"),
        (INCORRECT, "address"),
        (UNEXPECTED_FIELD, "address"),
        (MISSING_FIELD, "address.zip"),
        (INCORRECT, "address.zip"),
        (INCORRECT, "address.zip"),
        (INCORRECT, "address.zip"),
        (SELECT, None),
        (UNKNOWN, "invalid"),
    ]
    assert second == [
        (DEFAULT, None, doc("{ b { id title } }")),
        (NULL, "address", doc("{ b(address: null) { id title } }")),
        (ONLY_REQUIRED, "address", doc('{ b(address: {zip: ""}) { id title } }')),
        (INCORRECT, "address", doc("{ b(address: 1) { id title } }")),
        (UNEXPECTED_FIELD, "address", doc('{ b(address: {zip: "", invalid: 0}) { id title } }')),
        (UNKNOWN, "invalid", doc("{ b(invalid: 0) { id title } }")),
    ]


def test_shared_cases_keep_list_and_item_apart(ctx):
    schema = ctx.graphql.load_sdl("input Address { tags: [Int!]! } type Query { f(address: Address): Int }")
    assert [(parameter, body) for _, parameter, body in negative(schema, "Query.f", INCORRECT) if "null" in body] == [
        ("address.tags", doc("{ f(address: {tags: null}) }")),
        ("address.tags.0", doc("{ f(address: {tags: [null]}) }")),
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
        cases = schema.iter_coverage_cases(
            operation, generation_modes=list(GenerationMode), generation_config=schema.config.generation
        )
        with contextlib.suppress(UnknownScalar):
            for case in cases:
                data = case.meta.phase.data
                try:
                    case.call_and_validate(
                        checks=[negative_data_rejection, positive_data_acceptance, response_schema_conformance]
                    )
                    failure = None
                except FailureGroup as exc:
                    failure = tuple(type(inner).__name__ for inner in exc.exceptions)
                results.append((case.meta.generation.mode, data.scenario, data.parameter, failure))
    return results


@pytest.mark.parametrize("app", ["coverage_inputs", "books"])
def test_conforming_server_has_no_false_positives(ctx, app):
    results = run_against(getattr(ctx.graphql.apps, app)())
    assert len(results) > 1
    assert [result for result in results if result[3] is not None] == []


def test_server_rejects_cases_for_arguments_with_unknown_scalars(ctx):
    negative, positive = GenerationMode.NEGATIVE, GenerationMode.POSITIVE
    assert run_against(ctx.graphql.apps.use_after_create()) == [
        (negative, MISSING, "id", None),
        (negative, INCORRECT, "id", None),
        (positive, DEFAULT, None, None),
        (negative, MISSING, "title", None),
        (negative, INCORRECT, "title", None),
        (negative, INCORRECT, "title", None),
        (negative, INCORRECT, "title", None),
        (negative, UNKNOWN, "invalid", None),
        (negative, MISSING, "id", None),
        (negative, INCORRECT, "id", None),
    ]


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
