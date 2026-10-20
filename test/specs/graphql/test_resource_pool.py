from __future__ import annotations

import json
import random

import graphql
import pytest

from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.graphql.extra_data_source import GraphQLResourcePool
from schemathesis.specs.graphql.handles import Handle, SchemaIndex
from schemathesis.specs.graphql.substitution import iter_operation_pool_values, substitute_pool_values
from test.utils import run_given


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


@pytest.fixture
def pool(gql_schema):
    return GraphQLResourcePool(client_schema=gql_schema)


def _parse(query: str) -> graphql.OperationDefinitionNode:
    document = graphql.parse(query)
    operation = document.definitions[0]
    assert isinstance(operation, graphql.OperationDefinitionNode)
    return operation


def _schema_and_pool(sdl, **kwargs):
    schema = graphql.build_schema(sdl)
    return schema, GraphQLResourcePool(client_schema=schema, **kwargs)


def _capture_book(pool, book_id="captured-id"):
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x") { id } }'),
        response_data={"addBook": {"id": book_id}},
    )


def test_capture_id_field(pool, rng):
    operation = _parse('mutation { addBook(title: "x", authorId: "1") { id title } }')
    pool.capture(operation_node=operation, response_data={"addBook": {"id": "abc-1", "title": "x"}})
    assert pool.draw(handle=Handle("Book", "id"), random=rng) == "abc-1"


_RECORD_SDL = """
type Book { id: ID! title: String! }
type Query { getBooks: [Book!]! }
type Mutation { addBook(title: String!): Book! }
"""
_CREATED_BOOK = json.dumps({"data": {"addBook": {"id": "b-1", "__typename": "Book"}}}).encode("utf-8")


@pytest.mark.parametrize(
    ("query", "content"),
    [
        ('mutation { addBook(title: "x") { id } }', b""),
        ('mutation { addBook(title: "x") { id } }', b"not json"),
        ('mutation { addBook(title: "x") { id } }', b"{}"),
        ('mutation { addBook(title: "x") { id } }', b'{"data": null}'),
        ('mutation { addBook(title: "x") { id } }', b'{"data": "scalar"}'),
        (
            'mutation { addBook(title: "x") { id } }',
            json.dumps({"data": {"addBook": {"id": "b-1"}}, "errors": [{"message": "boom"}]}).encode("utf-8"),
        ),
        ("mutation { addBook(title: ", _CREATED_BOOK),
        ("fragment F on Book { id }", _CREATED_BOOK),
    ],
    ids=[
        "empty-body",
        "non-json-body",
        "empty-object",
        "data-null",
        "data-not-dict",
        "errors-present",
        "unparsable-query",
        "no-operation",
    ],
)
def test_record_response_ignores_unusable_payloads(ctx, response_factory, rng, query, content):
    schema = ctx.graphql.load_sdl(_RECORD_SDL)
    operation = schema["Mutation"]["addBook"]
    pool = GraphQLResourcePool(client_schema=schema.client_schema)
    response = Response.from_requests(response_factory.requests(content=content), True)
    pool.record_response(operation=operation, response=response, case=operation.Case(body=query))
    assert pool.draw(handle=Handle("Book", "id"), random=rng) is None


def test_record_response_skips_typename_selection(ctx, response_factory, rng):
    schema = ctx.graphql.load_sdl(_RECORD_SDL)
    operation = schema["Mutation"]["addBook"]
    pool = GraphQLResourcePool(client_schema=schema.client_schema)
    response = Response.from_requests(response_factory.requests(content=_CREATED_BOOK), True)
    pool.record_response(
        operation=operation,
        response=response,
        case=operation.Case(body='mutation { addBook(title: "x") { __typename id } }'),
    )
    assert pool.draw(handle=Handle("Book", "id"), random=rng) == "b-1"


def test_capture_walks_lists(pool, rng):
    operation = _parse("query { authors { id name } }")
    data = {"authors": [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]}
    pool.capture(operation_node=operation, response_data=data)
    drawn = {pool.draw(handle=Handle("Author", "id"), random=rng) for _ in range(20)}
    assert drawn == {"1", "2"}


def test_capture_uses_real_field_name_for_aliased_selections(pool, rng):
    operation = _parse('query { my: book(id: "1") { id } }')
    pool.capture(operation_node=operation, response_data={"my": {"id": "real-id"}})
    assert pool.draw(handle=Handle("Book", "id"), random=rng) == "real-id"


def test_per_key_cap_evicts_oldest(gql_schema, rng):
    pool = GraphQLResourcePool(client_schema=gql_schema, max_per_key=3)
    operation = _parse("query { authors { id } }")
    for chunk in [["1", "2"], ["3", "4"], ["5"]]:
        pool.capture(operation_node=operation, response_data={"authors": [{"id": v} for v in chunk]})
    drawn = set()
    for _ in range(50):
        v = pool.draw(handle=Handle("Author", "id"), random=rng)
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
    schema, pool = _schema_and_pool(sdl)
    if captured:
        _capture_book(pool)
    operation = _parse('query { book(id: "placeholder") { id } }')
    operation = substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    if should_substitute:
        assert "captured-id" in printed
        assert "placeholder" not in printed
    else:
        assert "placeholder" in printed
        assert "captured-id" not in printed


@pytest.mark.parametrize(
    ("sdl", "query"),
    [
        *(
            pytest.param(
                f"""
    type Book {{ id: ID! }}
    type Query {{ lookup({arg_name}: ID!): String }}
    type Mutation {{ addBook(title: String!): Book! }}
    """,
                f'query {{ lookup({arg_name}: "placeholder") }}',
                id=f"argument-name-token-{arg_name}",
            )
            for arg_name in ("bookId", "bookID", "book_id")
        ),
        *(
            pytest.param(
                f"""
    scalar BookID
    input BookRef {{ {input_field}: {field_type} }}
    type Book {{ id: BookID! }}
    type Query {{ lookup(ref: BookRef!): String }}
    type Mutation {{ addBook(title: String!): Book! }}
    """,
                f'query {{ lookup(ref: {{{input_field}: "placeholder"}}) }}',
                id=f"input-object-{case_id}",
            )
            for input_field, field_type, case_id in (
                ("id", "BookID", "bespoke-scalar"),
                ("bookId", "ID!", "camelCase-name-token"),
                ("book_id", "ID!", "snake_case-name-token"),
            )
        ),
        pytest.param(
            """
    scalar BookID
    input BookRef { id: BookID! }
    input UpdateInput { ref: BookRef!, title: String! }
    type Book { id: BookID! }
    type Query { _: Int }
    type Mutation {
        addBook(title: String!): Book!
        updateBook(input: UpdateInput!): Book
    }
    """,
            'mutation { updateBook(input: {ref: {id: "placeholder"}, title: "t"}) { id } }',
            id="nested-input-object",
        ),
        # A fragment spread next to `id` inside the selection set does not break the walk.
        pytest.param(
            """
    scalar BookID
    type Book { id: BookID! title: String! }
    type Query { book(id: BookID!): Book }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { book(id: "placeholder") { id ...BookFields } } fragment BookFields on Book { title }',
            id="fragment-spread-inside-selection",
        ),
        pytest.param(
            """
    scalar BookID
    type Author { books(bookId: BookID!): [String!]! }
    type Book { id: BookID! }
    type Query { author: Author book(id: BookID!): Book }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { author { books(bookId: "placeholder") } }',
            id="nested-object-selection",
        ),
    ],
)
def test_substitution_replaces(sdl, query, rng):
    schema, pool = _schema_and_pool(sdl)
    _capture_book(pool)
    operation = substitute_pool_values(operation_node=_parse(query), client_schema=schema, pool=pool, random=rng)
    assert "captured-id" in graphql.print_ast(operation)


@pytest.mark.parametrize(
    ("sdl", "query", "captured", "kept"),
    [
        pytest.param(
            """
    type Book { id: ID! }
    type Query { lookup(id: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { lookup(id: "placeholder") }',
            "captured-id",
            "placeholder",
            id="bare-id-when-field-returns-scalar",
        ),
        pytest.param(
            """
    type Book { id: ID! }
    type Query { lookup(filter: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { lookup(filter: "placeholder") }',
            "captured-id",
            "placeholder",
            id="generic-id-arg-with-non-id-name",
        ),
        pytest.param(
            """
    type Book { id: ID! }
    type Author { id: ID! }
    type Query { lookup(authorId: ID!): String }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { lookup(authorId: "placeholder") }',
            "book-id",
            "placeholder",
            id="isolated-by-argument-name-token",
        ),
        pytest.param(
            """
    scalar BookID
    scalar AuthorID
    type Book { id: BookID! }
    type Author { id: AuthorID! }
    type Query { author(id: AuthorID!): Author }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { author(id: "placeholder") { id } }',
            "book-id-only",
            "placeholder",
            id="isolated-by-parent-type",
        ),
        pytest.param(
            """
    scalar BookID
    type Book { id: BookID! }
    type Query { bookByTitle(title: String!): Book }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { bookByTitle(title: "placeholder") { id } }',
            "captured-id",
            "placeholder",
            id="non-id-scalar-argument",
        ),
        pytest.param(
            """
    scalar BookID
    input BookFilter { title: String }
    type Book { id: BookID! }
    type Query { books(filter: BookFilter!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { books(filter: {title: "placeholder"}) { id } }',
            "captured-id",
            "placeholder",
            id="non-id-input-field",
        ),
        pytest.param(
            """
    enum Status { ACTIVE INACTIVE }
    type Book { id: ID! }
    type Query { books(status: Status!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """,
            "query { books(status: ACTIVE) { id } }",
            "captured-id",
            "ACTIVE",
            id="enum-typed-argument",
        ),
        pytest.param(
            """
    scalar BookID
    scalar AuthorID
    input AuthorRef { id: AuthorID! }
    type Book { id: BookID! }
    type Author { id: AuthorID! }
    type Query { authorPosts(ref: AuthorRef!): [String!]! }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { authorPosts(ref: {id: "placeholder"}) }',
            "book-id",
            "placeholder",
            id="input-object-field-isolated-by-type",
        ),
        pytest.param(
            """
    scalar BookID
    type Book { id: BookID! }
    type Query { books(titles: [String!]!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """,
            'query { books(titles: ["a", "b"]) { id } }',
            "captured-id",
            '["a", "b"]',
            id="list-of-non-id-scalars",
        ),
        pytest.param(
            """
    scalar BookID
    type Book { id: BookID! }
    type Query { books(ids: [BookID!]!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """,
            "query { books(ids: []) { id } }",
            "captured-id",
            "ids: []",
            id="empty-list-argument",
        ),
    ],
)
def test_substitution_leaves_untouched(sdl, query, captured, kept, rng):
    schema, pool = _schema_and_pool(sdl)
    _capture_book(pool, captured)
    operation = substitute_pool_values(operation_node=_parse(query), client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    assert kept in printed
    assert captured not in printed


def test_capture_skips_subscription_operations(rng):
    sdl = """
    type Query { _: Int }
    type Subscription { event: String! }
    """
    schema, pool = _schema_and_pool(sdl)
    operation = _parse("subscription { event }")
    pool.capture(operation_node=operation, response_data={"event": "hello"})
    assert pool.draw(handle=Handle("Subscription", "id"), random=rng) is None


def test_capture_skips_missing_response_keys(pool, rng):
    operation = _parse("query { authors { id } }")
    pool.capture(operation_node=operation, response_data={})
    assert pool.draw(handle=Handle("Author", "id"), random=rng) is None


def test_capture_skips_fragment_spreads_in_selection_set(pool, rng):
    operation = _parse("query { authors { ...AuthorFields id } } fragment AuthorFields on Author { name }")
    pool.capture(operation_node=operation, response_data={"authors": [{"id": "captured"}]})
    assert pool.draw(handle=Handle("Author", "id"), random=rng) == "captured"


def test_capture_skips_enum_typed_fields(rng):
    sdl = """
    enum Status { ACTIVE INACTIVE }
    type Author { id: ID! status: Status! }
    type Query { authors: [Author!]! }
    type Mutation { _: Boolean }
    """
    schema, pool = _schema_and_pool(sdl)
    operation = _parse("query { authors { id status } }")
    pool.capture(
        operation_node=operation,
        response_data={"authors": [{"id": "a-1", "status": "ACTIVE"}]},
    )
    # Enum field is silently skipped; only the scalar `id` is captured.
    assert pool.draw(handle=Handle("Author", "id"), random=rng) == "a-1"


def test_substitution_skips_subscription_operations(rng):
    sdl = """
    type Query { _: Int }
    type Subscription { event: String! }
    """
    schema, pool = _schema_and_pool(sdl)
    operation = _parse("subscription { event }")
    operation = substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    # No-op: substitution silently skips operation types other than query/mutation.
    assert "event" in graphql.print_ast(operation)


_UNKNOWN_NODES_SDL = """
scalar BookID
input BookFilter { id: BookID }
type Book { id: BookID! title: String! }
type Query { book(id: BookID!): Book  books(filter: BookFilter): [Book!]! }
type Mutation { addBook(title: String!): Book! }
"""


def test_substitution_leaves_nodes_unknown_to_the_schema_untouched(rng):
    schema, pool = _schema_and_pool(_UNKNOWN_NODES_SDL)
    _capture_book(pool)
    operation = _parse(
        'query { __typename book(id: "placeholder", extra: "keep") '
        'books(filter: {id: "placeholder", unknown: "keep"}) { id } }'
    )
    operation = substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    assert graphql.print_ast(operation) == graphql.print_ast(
        _parse(
            'query { __typename book(id: "captured-id", extra: "keep") '
            'books(filter: {id: "captured-id", unknown: "keep"}) { id } }'
        )
    )


def test_iter_operation_pool_values_skips_nodes_unknown_to_the_schema():
    schema = graphql.build_schema(_UNKNOWN_NODES_SDL)
    operation = _parse(
        'query { __typename book(id: "abc", extra: "keep") books(filter: {id: "def", unknown: "x"}) { id } }'
    )
    assert list(iter_operation_pool_values(operation, schema)) == [
        (Handle("Book", "id"), "abc"),
        (Handle("Book", "id"), "def"),
    ]


@pytest.mark.parametrize(
    ("arg_name", "scalar_type"),
    [
        ("ids", "BookID"),
        ("bookIds", "ID"),
        ("bookIDs", "ID"),
        ("book_ids", "ID"),
    ],
    ids=["bespoke-list", "camelCase-Ids", "camelCase-IDs", "snake_case-_ids"],
)
def test_substitution_into_list_argument(arg_name, scalar_type, rng):
    sdl = f"""
    scalar BookID
    type Book {{ id: BookID! }}
    type Query {{ books({arg_name}: [{scalar_type}!]!): [Book!]! }}
    type Mutation {{ addBook(title: String!): Book! }}
    """
    schema, pool = _schema_and_pool(sdl)
    for value in ("id-1", "id-2"):
        _capture_book(pool, value)
    operation = _parse(f'query {{ books({arg_name}: ["a", "b"]) {{ id }} }}')
    operation = substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    # Both list elements get replaced by captured ids; original placeholders are gone.
    assert '"a"' not in printed and '"b"' not in printed
    assert any(captured in printed for captured in ("id-1", "id-2"))


def test_substitution_into_list_of_input_objects(rng):
    sdl = """
    scalar BookID
    input BookRef { id: BookID! }
    type Book { id: BookID! }
    type Query { lookup(refs: [BookRef!]!): [Book!]! }
    type Mutation { addBook(title: String!): Book! }
    """
    schema, pool = _schema_and_pool(sdl)
    _capture_book(pool)
    operation = _parse('query { lookup(refs: [{id: "a"}, {id: "b"}]) { id } }')
    operation = substitute_pool_values(operation_node=operation, client_schema=schema, pool=pool, random=rng)
    printed = graphql.print_ast(operation)
    assert '"a"' not in printed and '"b"' not in printed
    assert "captured-id" in printed


def test_tombstone_evicts_value_from_pool(pool, rng):
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x", authorId: "1") { id } }'),
        response_data={"addBook": {"id": "abc"}},
    )
    pool.tombstone(handle=Handle("Book", "id"), value="abc")
    assert pool.draw(handle=Handle("Book", "id"), random=rng) is None


def test_tombstone_blocks_subsequent_capture(pool, rng):
    pool.tombstone(handle=Handle("Book", "id"), value="abc")
    pool.capture(
        operation_node=_parse('mutation { addBook(title: "x", authorId: "1") { id } }'),
        response_data={"addBook": {"id": "abc"}},
    )
    assert pool.draw(handle=Handle("Book", "id"), random=rng) is None


def test_tombstone_preserves_other_values(pool, rng):
    operation = _parse("query { authors { id } }")
    pool.capture(
        operation_node=operation,
        response_data={"authors": [{"id": "a-1"}, {"id": "a-2"}, {"id": "a-3"}]},
    )
    pool.tombstone(handle=Handle("Author", "id"), value="a-2")
    drawn = {pool.draw(handle=Handle("Author", "id"), random=rng) for _ in range(20)}
    assert drawn == {"a-1", "a-3"}


_TOMBSTONE_SDL = """
scalar BookID
input BookRef { id: BookID! }
type Book { id: BookID! }
type Query { _: Int }
type Mutation {
    addBook(title: String!): Book!
    deleteBook(id: BookID!): Boolean
    deleteBooks(ids: [BookID!]!): Boolean
    deleteBookByRef(ref: BookRef!): Boolean
    deleteByName(name: String!): Boolean
}
"""

_TOMBSTONE_NESTED_SDL = """
scalar AuthorID
scalar BookID
type Author { id: AuthorID! }
type Book { id: BookID! relatedAuthor(authorId: AuthorID!): Author }
type Query { _: Int }
type Mutation { deleteBook(id: BookID!): Book }
"""

_TOMBSTONE_SUBSCRIPTION_SDL = """
type Query { _: Int }
type Subscription { event(id: ID!): String! }
"""


@pytest.mark.parametrize(
    ("sdl", "query", "expected"),
    [
        (_TOMBSTONE_SDL, 'mutation { deleteBook(id: "abc") }', [(Handle("Book", "id"), "abc")]),
        (
            _TOMBSTONE_SDL,
            'mutation { deleteBooks(ids: ["a", "b"]) }',
            [(Handle("Book", "id"), "a"), (Handle("Book", "id"), "b")],
        ),
        (_TOMBSTONE_SDL, 'mutation { deleteBookByRef(ref: {id: "abc"}) }', [(Handle("Book", "id"), "abc")]),
        (_TOMBSTONE_SDL, 'mutation { deleteByName(name: "foo") }', []),
        (_TOMBSTONE_SDL, "mutation { deleteBook(id: $var) }", []),
        (_TOMBSTONE_SDL, 'mutation { deleteBook(unknownArg: "noise", id: "x") }', [(Handle("Book", "id"), "x")]),
        (_TOMBSTONE_SDL, 'mutation { deleteBookByRef(ref: {id: "x", noise: "y"}) }', [(Handle("Book", "id"), "x")]),
        (_TOMBSTONE_SDL, 'mutation { unknownTopLevelField(id: "x") }', []),
        (
            _TOMBSTONE_NESTED_SDL,
            'mutation { deleteBook(id: "x") { relatedAuthor(authorId: "y") { id } } }',
            [(Handle("Book", "id"), "x"), (Handle("Author", "id"), "y")],
        ),
        (
            _TOMBSTONE_NESTED_SDL,
            'mutation { deleteBook(id: "x") { ...Frag } } fragment Frag on Book { id }',
            [(Handle("Book", "id"), "x")],
        ),
        (
            _TOMBSTONE_NESTED_SDL,
            'mutation { deleteBook(id: "x") { unknownNestedField } }',
            [(Handle("Book", "id"), "x")],
        ),
        (_TOMBSTONE_SUBSCRIPTION_SDL, 'subscription { event(id: "abc") }', []),
    ],
    ids=[
        "scalar",
        "list",
        "input-object",
        "non-id-arg",
        "variable-not-literal",
        "unknown-arg",
        "unknown-input-field",
        "unknown-top-level-field",
        "nested-object-recursion",
        "fragment-spread-skipped",
        "unknown-nested-field",
        "subscription",
    ],
)
def test_iter_operation_pool_values(sdl, query, expected):
    schema = graphql.build_schema(sdl)
    operation = _parse(query)
    assert list(iter_operation_pool_values(operation, schema)) == expected


def test_capture_and_draw_non_id_handle(rng):
    schema = graphql.build_schema(
        "type Project { id: ID! fullPath: String! }"
        " type Query { projects: [Project!]! }"
        " type Mutation { moveIssue(projectPath: String!, title: String!): Boolean }"
    )
    pool = GraphQLResourcePool(client_schema=schema, handles={Handle("Project", "fullPath")})
    pool.capture(
        operation_node=_parse("query { projects { id fullPath } }"),
        response_data={"projects": [{"id": "1", "fullPath": "acme/web"}]},
    )
    assert pool.draw(handle=Handle("Project", "fullPath"), random=rng) == "acme/web"
    # `id` is not in the active handle set, so it is not captured.
    assert pool.draw(handle=Handle("Project", "id"), random=rng) is None


def test_pool_substitutes_non_id_argument(rng):
    schema = graphql.build_schema(
        "type Project { id: ID! fullPath: String! }"
        " type Query { projects: [Project!]! }"
        " type Mutation { moveIssue(projectPath: String!, title: String!): Boolean }"
    )
    pool = GraphQLResourcePool(client_schema=schema, handles={Handle("Project", "fullPath")})
    pool.capture(
        operation_node=_parse("query { projects { id fullPath } }"),
        response_data={"projects": [{"id": "1", "fullPath": "acme/web"}]},
    )
    op = _parse('mutation { moveIssue(projectPath: "RANDOM", title: "x") }')
    op = substitute_pool_values(
        operation_node=op, client_schema=schema, pool=pool, random=rng, schema_index=SchemaIndex(schema)
    )
    assert 'projectPath: "acme/web"' in graphql.print_ast(op)


_POOL_MODE_SDL = (
    "type Author { id: ID! } type Book { id: ID! author: Author! } type Query { book(id: ID, authorId: ID): Book }"
)


def _pool_with_captured_book(schema):
    pool = GraphQLResourcePool(client_schema=schema.client_schema)
    pool.capture(
        operation_node=_parse("query { book { id author { id } } }"),
        response_data={"book": {"id": "captured-book", "author": {"id": "captured-author"}}},
    )
    return pool


@pytest.mark.hypothesis_nested
def test_negative_case_keeps_violation_when_pool_has_values(ctx):
    schema = ctx.graphql.load_sdl(_POOL_MODE_SDL)
    strategy = schema["Query"]["book"].as_strategy(
        generation_mode=GenerationMode.NEGATIVE,
        feedback=FeedbackSources(extra_data_source=_pool_with_captured_book(schema)),
    )

    @run_given(strategy, max_examples=10)
    def test(case):
        assert graphql.validate(schema.client_schema, graphql.parse(case.body)), (
            f"Negative case is schema-valid: {case.body}"
        )


@pytest.mark.hypothesis_nested
def test_positive_case_uses_pool_values(ctx):
    schema = ctx.graphql.load_sdl(_POOL_MODE_SDL)
    strategy = schema["Query"]["book"].as_strategy(
        feedback=FeedbackSources(extra_data_source=_pool_with_captured_book(schema))
    )
    bodies = [case.body for case in generate_many(strategy, 10)]

    assert any('id: "captured-book"' in body and 'authorId: "captured-author"' in body for body in bodies)
