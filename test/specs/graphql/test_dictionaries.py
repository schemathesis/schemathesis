from __future__ import annotations

import random
import re

import graphql
import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.generation import GenerationMode
from schemathesis.generation.dictionaries import DictionaryDraw
from schemathesis.generation.meta import TestPhase
from schemathesis.python._constants.pool import ConstantEntry, ConstantsPool, Origin
from schemathesis.specs.graphql.dictionaries import resolve_bindings, substitute_dictionaries
from schemathesis.specs.graphql.schemas import graphql_cases

SDL = """
interface Media {
  title: String!
}

type Book implements Media {
  title: String!
}

type Magazine implements Media {
  title: String!
  issue: Int!
}

enum Genre {
  SCIFI
  FANTASY
}

type Author {
  name: String!
  books(limit: Int!, genre: String!): [Book!]!
}

input OrderInput {
  currency: String!
  quantity: Int!
}

type Order {
  id: ID!
}

type Query {
  bookByTitle(title: String!): Book
  search(tags: [String!]!): [Book!]!
  authorByName(name: String!): Author
  rated(score: Float!, verified: Boolean!, genre: Genre!): [Book!]!
  media(title: String!): Media
}

type Mutation {
  createOrder(input: OrderInput!): Order!
}
"""

TITLE_BINDING: dict = {
    "dictionaries": {"titles": {"values": ["Dune"]}},
    "parameters": {"Query.bookByTitle.title": {"dictionary": "titles"}},
}


def load(ctx, config: dict):
    return ctx.graphql.load_sdl(SDL, config=SchemathesisConfig.from_dict(config))


def collect_bodies(operation, *, mode=GenerationMode.POSITIVE, max_examples=10, containing=""):
    bodies = []

    @given(case=operation.as_strategy(generation_mode=mode))
    @settings(max_examples=max_examples, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def run(case):
        bodies.append(case.body)

    run()
    return [body for body in bodies if containing in body]


@pytest.mark.parametrize(
    ("config", "root", "field", "expected", "containing"),
    [
        (
            {
                "dictionaries": {"titles": {"values": ["Dune", "Solaris"]}},
                "parameters": {"Query.bookByTitle.title": {"dictionary": "titles"}},
            },
            "Query",
            "bookByTitle",
            r'title: "(Dune|Solaris)"',
            "",
        ),
        (
            {
                "dictionaries": {"titles": {"values": ["Dune"]}},
                "parameters": {"*.*.title": {"dictionary": "titles"}},
            },
            "Query",
            "bookByTitle",
            r'title: "Dune"',
            "",
        ),
        (
            {
                "dictionaries": {"titles": {"values": ["Dune"]}},
                "parameters": {"Query.*.title": {"dictionary": "titles"}},
            },
            "Query",
            "bookByTitle",
            r'title: "Dune"',
            "",
        ),
        (
            {
                "dictionaries": {"currencies": {"values": ["USD", "EUR"]}},
                "parameters": {"Mutation.createOrder.input.currency": {"dictionary": "currencies"}},
            },
            "Mutation",
            "createOrder",
            r'currency: "(USD|EUR)"',
            "",
        ),
        (
            {
                "dictionaries": {"genres": {"values": ["scifi"]}},
                "parameters": {"Author.books.genre": {"dictionary": "genres"}},
            },
            "Query",
            "authorByName",
            r'genre: "scifi"',
            "genre:",
        ),
        (
            {
                "dictionaries": {"words": {"values": ["admin"]}},
                "generation": {"dictionaries": {"string": {"dictionary": "words", "probability": 1.0}}},
            },
            "Query",
            "bookByTitle",
            r'title: "admin"',
            "",
        ),
        (
            {
                "dictionaries": {"specific": {"values": ["Dune"]}, "generic": {"values": ["Solaris"]}},
                "parameters": {
                    "*.*.title": {"dictionary": "generic"},
                    "Query.bookByTitle.title": {"dictionary": "specific"},
                },
            },
            "Query",
            "bookByTitle",
            r'title: "Dune"',
            "",
        ),
        (
            {
                "dictionaries": {"titles": {"values": ["Dune"]}},
                "parameters": {
                    "api_version": "v2",
                    "query.q": {"dictionary": "titles"},
                    "Query.bookByTitle.title": {"dictionary": "titles"},
                },
            },
            "Query",
            "bookByTitle",
            r'title: "Dune"',
            "",
        ),
        (
            {
                "dictionaries": {"scores": {"values": ["1.5", "inf", "nope"]}},
                "parameters": {"Query.rated.score": {"dictionary": "scores"}},
            },
            "Query",
            "rated",
            r"score: 1\.5",
            "",
        ),
        (
            {
                "dictionaries": {"titles": {"values": ["Dune"]}},
                "parameters": {"Query.media.title": {"dictionary": "titles"}},
            },
            "Query",
            "media",
            r'media\(title: "Dune"\)',
            "... on ",
        ),
    ],
    ids=[
        "argument-key",
        "type-and-field-wildcard",
        "field-wildcard",
        "input-object-path",
        "nested-field-argument",
        "type-wide-binding",
        "specific-beats-wildcard",
        "location-shaped-keys-ignored",
        "float-argument",
        "interface-returning-field",
    ],
)
@pytest.mark.hypothesis_nested
def test_binding_fills_argument(ctx, config, root, field, expected, containing):
    bodies = collect_bodies(load(ctx, config)[root][field], containing=containing)
    assert bodies and all(re.search(expected, body) for body in bodies)


@pytest.mark.hypothesis_nested
def test_every_list_element_is_filled(ctx):
    schema = load(
        ctx,
        {
            "dictionaries": {"tags": {"values": ["sale"]}},
            "parameters": {"Query.search.tags[*]": {"dictionary": "tags"}},
        },
    )
    bodies = collect_bodies(schema["Query"]["search"])
    assert bodies and all(set(re.findall(r'"[^"]*"', body)) <= {'"sale"'} for body in bodies)


@pytest.mark.parametrize(
    ("config", "root", "field", "forbidden", "containing", "mode"),
    [
        (
            {
                "dictionaries": {"words": {"values": ["not-a-number"]}},
                "parameters": {"Author.books.limit": {"dictionary": "words"}},
            },
            "Query",
            "authorByName",
            "not-a-number",
            "limit:",
            GenerationMode.POSITIVE,
        ),
        (
            {
                "dictionaries": {"tags": {"values": ["sale"]}},
                "parameters": {"Query.search.tags": {"dictionary": "tags"}},
            },
            "Query",
            "search",
            "sale",
            "",
            GenerationMode.POSITIVE,
        ),
        (TITLE_BINDING, "Query", "bookByTitle", 'title: "Dune"', "", GenerationMode.NEGATIVE),
    ],
    ids=["entries-not-matching-scalar-type", "list-without-element-wildcard", "negative-mode"],
)
@pytest.mark.hypothesis_nested
def test_binding_leaves_argument_alone(ctx, config, root, field, forbidden, containing, mode):
    bodies = collect_bodies(load(ctx, config)[root][field], mode=mode, containing=containing)
    assert bodies and all(forbidden not in body for body in bodies)


@pytest.mark.parametrize(
    "expected",
    [r"verified: (true|false)", r"genre: (SCIFI|FANTASY)\b"],
    ids=["boolean", "enum"],
)
@pytest.mark.hypothesis_nested
def test_unsupported_scalar_kinds_keep_generated_values(ctx, expected):
    schema = load(
        ctx,
        {
            "dictionaries": {"words": {"values": ["SCIFI"]}},
            "generation": {"dictionaries": {"string": {"dictionary": "words", "probability": 1.0}}},
            "parameters": {"Query.rated.verified": {"dictionary": "words"}},
        },
    )
    bodies = collect_bodies(schema["Query"]["rated"])
    assert bodies and all(re.search(expected, body) for body in bodies)


def substitute(schema, query, *, root="Query", field="bookByTitle"):
    operation = schema[root][field]
    generation = schema.config.generation_for(operation=operation, phase="fuzzing")
    return substitute_dictionaries(
        operation_node=graphql.parse(query).definitions[0],
        client_schema=schema.client_schema,
        bindings=resolve_bindings(operation, generation),
        operation_label=operation.label,
        random=random.Random(0),
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('{ bookByTitle(title: "x") { ... on Book { title } } }', ["Dune"]),
        ('{ bookByTitle(title: "x") { __typename } }', ["Dune"]),
        ('{ bookByTitle(title: "x") { title } }', ["Dune"]),
        ('subscription { bookByTitle(title: "x") { title } }', []),
    ],
    ids=["inline-fragment", "meta-field", "plain-field", "subscription-root"],
)
def test_substitution_across_selection_shapes(ctx, query, expected):
    assert [draw.value for draw in substitute(load(ctx, TITLE_BINDING), query)] == expected


@pytest.mark.hypothesis_nested
def test_draw_provenance_attached(ctx):
    schema = load(ctx, TITLE_BINDING)
    draws = []

    @given(case=schema["Query"]["bookByTitle"].as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def run(case):
        draws.extend(case._meta.dictionary_draws)

    run()
    assert draws and set(draws) == {
        DictionaryDraw(
            dictionary="titles",
            source_kind="values",
            source_path=None,
            entry_index=0,
            operation_label="Query.bookByTitle",
            parameter_location="body",
            parameter_name="title",
            value="Dune",
            matches_schema=True,
            body_path="/bookByTitle/title",
        )
    }


@pytest.mark.hypothesis_nested
def test_no_dictionaries_configured_is_a_no_op(ctx):
    schema = load(ctx, {})
    draws = []

    @given(case=schema["Query"]["bookByTitle"].as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def run(case):
        draws.extend(case._meta.dictionary_draws)

    run()
    assert draws == []


@pytest.mark.hypothesis_nested
def test_probability_below_one_leaves_some_values_alone(ctx):
    schema = load(
        ctx,
        {
            "dictionaries": {"titles": {"values": ["Dune"]}},
            "parameters": {"Query.bookByTitle.title": {"dictionary": "titles", "probability": 0.5}},
        },
    )
    bodies = collect_bodies(schema["Query"]["bookByTitle"], max_examples=30)
    assert any('title: "Dune"' in body for body in bodies)
    assert any('title: "Dune"' not in body for body in bodies)


@pytest.mark.hypothesis_nested
def test_binding_applies_in_stateful_phase(ctx):
    schema = load(ctx, TITLE_BINDING)
    bodies = []

    @given(case=graphql_cases(operation=schema["Query"]["bookByTitle"], phase=TestPhase.STATEFUL))
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def run(case):
        bodies.append(case.body)

    run()
    assert bodies and all('title: "Dune"' in body for body in bodies)


@pytest.mark.hypothesis_nested
def test_dictionary_entry_survives_the_constants_pool(ctx):
    schema = load(ctx, TITLE_BINDING)
    pool = ConstantsPool()
    pool.add(ConstantEntry(value="HARVESTED", type="string", origins=(Origin(source="s", module="m", adapter=None),)))
    bodies = []

    @given(case=graphql_cases(operation=schema["Query"]["bookByTitle"], constants_value_source=pool))
    @settings(max_examples=20, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def run(case):
        bodies.append(case.body)

    run()
    assert bodies and all('title: "Dune"' in body for body in bodies)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("quer.q", "unknown location prefix"),
        ("Query.bookByTitle.", re.escape("must be `<Type>.<field>.<argument>`")),
        ("Query.book-by-title.title", "Invalid segment"),
        ("Query.bookByTitle.tags[1]", re.escape("Only `[*]` is supported")),
    ],
    ids=["two-segment-typo", "trailing-dot", "invalid-name", "positional-index"],
)
def test_invalid_binding_keys(key, expected):
    with pytest.raises(ConfigError, match=expected):
        SchemathesisConfig.from_dict(
            {"dictionaries": {"d": {"values": ["x"]}}, "parameters": {key: {"dictionary": "d"}}}
        )
