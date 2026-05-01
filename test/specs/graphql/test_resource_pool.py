from __future__ import annotations

import json
import random

import graphql
import pytest

from schemathesis.specs.graphql.extra_data_source import GraphQLResourcePool
from schemathesis.specs.graphql.substitution import substitute_pool_values


@pytest.fixture
def gql_schema():
    sdl = """
    scalar BookID
    type Author { id: ID! name: String! }
    type Book { id: BookID! title: String! author: Author! }
    type Query {
        book(id: BookID!): Book
        authors: [Author!]!
    }
    type Mutation {
        addBook(title: String!, authorId: ID!): Book!
    }
    """
    return graphql.build_schema(sdl)


@pytest.fixture
def rng():
    return random.Random(0)


def _parse(query: str) -> graphql.OperationDefinitionNode:
    document = graphql.parse(query)
    operation = document.definitions[0]
    assert isinstance(operation, graphql.OperationDefinitionNode)
    return operation


def test_capture_id_field(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse('mutation { addBook(title: "x", authorId: "1") { id title } }')
    pool.capture(operation_node=operation, response_data={"addBook": {"id": "abc-1", "title": "x"}})
    assert pool.draw(parent_type_name="Book", random=rng) == "abc-1"


def test_capture_skips_when_errors_present(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse('mutation { addBook(title: "x", authorId: "1") { id } }')
    body = json.dumps({"data": {"addBook": {"id": "abc-2"}}, "errors": [{"message": "boom"}]}).encode("utf-8")
    pool.capture_response(response_body=body, operation_node=operation)
    assert pool.draw(parent_type_name="Book", random=rng) is None


def test_capture_walks_lists(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse("query { authors { id name } }")
    data = {"authors": [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]}
    pool.capture(operation_node=operation, response_data=data)
    drawn = {pool.draw(parent_type_name="Author", random=rng) for _ in range(20)}
    assert drawn == {"1", "2"}


def test_capture_uses_real_field_name_for_aliased_selections(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse('query { my: book(id: "1") { id } }')
    pool.capture(operation_node=operation, response_data={"my": {"id": "real-id"}})
    assert pool.draw(parent_type_name="Book", random=rng) == "real-id"


def test_per_key_cap_evicts_oldest(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema, max_per_key=3)
    operation = _parse("query { authors { id } }")
    for chunk in [["1", "2"], ["3", "4"], ["5"]]:
        pool.capture(operation_node=operation, response_data={"authors": [{"id": v} for v in chunk]})
    drawn = set()
    for _ in range(50):
        v = pool.draw(parent_type_name="Author", random=rng)
        if v is not None:
            drawn.add(v)
    assert "1" not in drawn and "2" not in drawn
    assert drawn <= {"3", "4", "5"}


_BESPOKE_ID_SDL = """
scalar BookID
type Book { id: BookID! title: String! }
type Query { book(id: BookID!): Book }
type Mutation { addBook(title: String!): Book! }
"""

_GENERIC_ID_BARE_ARG_SDL = """
type Book { id: ID! }
type Query { book(id: ID!): Book }
type Mutation { addBook(title: String!): Book! }
"""


@pytest.mark.parametrize(
    ("sdl", "captured", "should_substitute"),
    [
        (_BESPOKE_ID_SDL, True, True),
        (_BESPOKE_ID_SDL, False, False),
        (_GENERIC_ID_BARE_ARG_SDL, True, True),
    ],
    ids=["bespoke-scalar-substitutes", "empty-pool-skips", "bare-id-via-return-type"],
)
def test_substitution_type_match(sdl, captured, should_substitute, rng):
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    if captured:
        pool.capture(
            operation_node=_parse('mutation { addBook(title: "x") { id } }'),
            response_data={"addBook": {"id": "captured-id"}},
        )
    operation = _parse('query { book(id: "placeholder") { id } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    if should_substitute:
        assert "captured-id" in printed
        assert "placeholder" not in printed
    else:
        assert "placeholder" in printed
        assert "captured-id" not in printed


@pytest.mark.parametrize("arg_name", ["bookId", "bookID", "book_id"])
def test_substitution_via_argument_name_token(arg_name, rng):
    sdl = f"""
    type Book {{ id: ID! }}
    type Query {{ lookup({arg_name}: ID!): String }}
    type Mutation {{ addBook(title: String!): Book! }}
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse(f'query {{ lookup({arg_name}: "placeholder") }}')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "captured-id" in graphql.print_ast(operation)


def test_substitution_skips_bare_id_when_field_returns_scalar(rng):
    sdl = """
    type Book { id: ID! }
    type Query { lookup(id: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('query { lookup(id: "placeholder") }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "placeholder" in graphql.print_ast(operation)


def test_substitution_skips_generic_id_arg_with_non_id_name(rng):
    sdl = """
    type Book { id: ID! }
    type Query { lookup(filter: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('query { lookup(filter: "placeholder") }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "placeholder" in graphql.print_ast(operation)


def test_substitution_isolates_by_argument_name_token(rng):
    sdl = """
    type Book { id: ID! }
    type Author { id: ID! }
    type Query { lookup(authorId: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "book-id"}},
    )
    operation = _parse('query { lookup(authorId: "placeholder") }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "placeholder" in graphql.print_ast(operation)


def test_substitution_isolates_by_parent_type(rng):
    sdl = """
    scalar BookID
    scalar AuthorID
    type Book { id: BookID! }
    type Author { id: AuthorID! }
    type Query { author(id: AuthorID!): Author }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "book-id-only"}},
    )
    operation = _parse('query { author(id: "placeholder") { id } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    assert "book-id-only" not in printed
    assert "placeholder" in printed


@pytest.mark.parametrize(
    "body",
    [b"not-json", b"{}", b'{"data": null}', b'{"data": "scalar"}'],
    ids=["malformed-json", "empty-object", "data-null", "data-not-dict"],
)
def test_capture_response_skips_unusable_payloads(gql_schema, rng, body):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse('mutation { addBook(title: "x", authorId: "1") { id } }')
    pool.capture_response(response_body=body, operation_node=operation)
    assert pool.draw(parent_type_name="Book", random=rng) is None


def test_capture_skips_subscription_operations(rng):
    sdl = """
    type Query { _: Int }
    type Subscription { event: String! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    operation = _parse("subscription { event }")
    pool.capture(operation_node=operation, response_data={"event": "hello"})
    assert pool.draw(parent_type_name="Subscription", random=rng) is None


def test_capture_skips_missing_response_keys(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse("query { authors { id } }")
    pool.capture(operation_node=operation, response_data={})
    assert pool.draw(parent_type_name="Author", random=rng) is None


def test_capture_skips_fragment_spreads_in_selection_set(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema)
    operation = _parse("query { authors { ...AuthorFields id } } fragment AuthorFields on Author { name }")
    pool.capture(operation_node=operation, response_data={"authors": [{"id": "captured"}]})
    assert pool.draw(parent_type_name="Author", random=rng) == "captured"


def test_capture_skips_enum_typed_fields(rng):
    sdl = """
    enum Status { ACTIVE INACTIVE }
    type Author { id: ID! status: Status! }
    type Query { authors: [Author!]! }
    type Mutation { _: Boolean }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    operation = _parse("query { authors { id status } }")
    pool.capture(
        operation_node=operation,
        response_data={"authors": [{"id": "a-1", "status": "ACTIVE"}]},
    )
    # Enum field is silently skipped; only the scalar `id` is captured.
    assert pool.draw(parent_type_name="Author", random=rng) == "a-1"


def test_substitution_skips_subscription_operations(rng):
    sdl = """
    type Query { _: Int }
    type Subscription { event: String! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    operation = _parse("subscription { event }")
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    # No-op: substitution silently skips operation types other than query/mutation.
    assert "event" in graphql.print_ast(operation)


def test_substitution_skips_non_id_scalar_arguments(rng):
    sdl = """
    scalar BookID
    type Book { id: BookID! }
    type Query { bookByTitle(title: String!): Book }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('query { bookByTitle(title: "placeholder") { id } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    # `title: String!` is not an ID-shaped argument; substitution leaves it.
    assert "placeholder" in graphql.print_ast(operation)


def test_substitution_leaves_non_id_input_fields_untouched(rng):
    sdl = """
    scalar BookID
    input BookFilter { title: String }
    type Book { id: BookID! }
    type Query { books(filter: BookFilter!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('query { books(filter: {title: "placeholder"}) { id } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "placeholder" in graphql.print_ast(operation)


@pytest.mark.parametrize(
    ("input_field", "field_type"),
    [
        ("id", "BookID"),
        ("bookId", "ID!"),
        ("book_id", "ID!"),
    ],
    ids=["bespoke-scalar", "camelCase-name-token", "snake_case-name-token"],
)
def test_substitution_descends_into_input_object(input_field, field_type, rng):
    sdl = f"""
    scalar BookID
    input BookRef {{ {input_field}: {field_type} }}
    type Book {{ id: BookID! }}
    type Query {{ lookup(ref: BookRef!): String }}
    type Mutation {{ addBook(title: String!): Book! }}
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse(f'query {{ lookup(ref: {{{input_field}: "placeholder"}}) }}')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "captured-id" in graphql.print_ast(operation)


def test_substitution_skips_enum_typed_arguments(rng):
    sdl = """
    enum Status { ACTIVE INACTIVE }
    type Book { id: ID! }
    type Query { books(status: Status!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse("query { books(status: ACTIVE) { id } }")
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "ACTIVE" in graphql.print_ast(operation)


def test_substitution_descends_into_nested_input_object(rng):
    sdl = """
    scalar BookID
    input BookRef { id: BookID! }
    input UpdateInput { ref: BookRef!, title: String! }
    type Book { id: BookID! }
    type Query { _: Int }
    type Mutation {
        addBook(title: String!): Book!
        updateBook(input: UpdateInput!): Book
    }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('mutation { updateBook(input: {ref: {id: "placeholder"}, title: "t"}) { id } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "captured-id" in graphql.print_ast(operation)


def test_substitution_isolates_input_object_field_by_type(rng):
    sdl = """
    scalar BookID
    scalar AuthorID
    input AuthorRef { id: AuthorID! }
    type Book { id: BookID! }
    type Author { id: AuthorID! }
    type Query { authorPosts(ref: AuthorRef!): [String!]! }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "book-id"}},
    )
    operation = _parse('query { authorPosts(ref: {id: "placeholder"}) }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "placeholder" in graphql.print_ast(operation)


def test_substitution_skips_fragment_spreads_inside_selection(rng):
    sdl = """
    scalar BookID
    type Book { id: BookID! title: String! }
    type Query { book(id: BookID!): Book }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    # Fragment spread next to `id` inside book's selection set; the walker skips it.
    operation = _parse('query { book(id: "placeholder") { id ...BookFields } } fragment BookFields on Book { title }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    # The outer arg is substituted; the fragment spread doesn't break the walk.
    assert "captured-id" in graphql.print_ast(operation)


def test_substitution_walks_into_nested_object_selections(rng):
    sdl = """
    scalar BookID
    type Author { books(bookId: BookID!): [String!]! }
    type Book { id: BookID! }
    type Query { author: Author book(id: BookID!): Book }
    type Mutation { addBook(title: String!): Book! }
    """
    schema = graphql.build_schema(sdl)
    pool = GraphQLResourcePool(client_schema=schema)
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": "captured-id"}},
    )
    operation = _parse('query { author { books(bookId: "placeholder") } }')
    substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert "captured-id" in graphql.print_ast(operation)
