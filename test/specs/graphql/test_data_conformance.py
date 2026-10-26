import pytest
import requests

from schemathesis.core.failures import FailureGroup
from schemathesis.graphql.checks import GraphQLDataMismatch, GraphQLServerError

SDL = """
scalar DateTime
enum Genre { FICTION SCIENCE }
interface Node { id: ID! }
type Book implements Node {
  id: ID!
  title: String!
  pages: Int
  rating: Float
  genre: Genre
  published: DateTime
  tags: [String!]!
  author: Author
}
type Author { name: String! }
union Item = Book | Author
type Query {
  book: Book
  books: [Book!]
  items: [Item!]!
  node: Node
}
"""

IMPLEMENTATIONS = 10
DEPTH = 8
NESTED_SDL = (
    "interface Node { id: ID! child: Node }\n"
    + "".join(
        f"type T{index} implements Node {{ id: ID! child: Node f{index}: Int }}\n" for index in range(IMPLEMENTATIONS)
    )
    + "type Query { node: Node }"
)
LAST = IMPLEMENTATIONS - 1


def _nested_query(selection):
    query = selection
    for _ in range(DEPTH - 1):
        query = f"{selection} child {{ {query} }}"
    return f"{{ node {{ {query} }} }}"


def _nested_data(leaf, extra):
    data = {**leaf, **extra}
    for _ in range(DEPTH - 1):
        data = {"id": "1", **extra, "child": data}
    return {"node": data}


def _call(ctx, field, query, payload, *, sdl=SDL, status_code=200):
    api = ctx.graphql.apps.canned_response(payload, status_code=status_code)
    case = ctx.graphql.load_sdl(sdl)["Query"][field].Case(body=query)
    case.call_and_validate(base_url=api.base_url)


def _failures(ctx, field, query, payload, **kwargs):
    with pytest.raises(FailureGroup) as exc:
        _call(ctx, field, query, payload, **kwargs)
    return [(type(failure), failure.message) for failure in exc.value.exceptions]


@pytest.mark.parametrize(
    ("field", "query", "data"),
    [
        (
            "book",
            "{ book { id title pages rating genre published tags author { name } } }",
            {
                "book": {
                    "id": "1",
                    "title": "Dune",
                    "pages": 412,
                    "rating": 4,
                    "genre": "FICTION",
                    "published": {"anything": "goes"},
                    "tags": ["sf"],
                    "author": None,
                }
            },
        ),
        ("book", "{ book { title } }", {"book": None}),
        ("book", "{ book { id } }", {"book": {"id": 1}}),
        ("book", "{ book { pages } }", {"book": {"pages": 412.0}}),
        ("book", "{ b: book { t: title } }", {"b": {"t": "Dune"}}),
        ("book", "{ book { title edition } }", {"book": {"title": "Dune", "edition": [1, "x"]}}),
        ("book", "{ book { __typename title } }", {"book": {"__typename": "Book", "title": "Dune"}}),
        ("book", "{ book { title pages @skip(if: true) } }", {"book": {"title": "Dune"}}),
        ("book", "{ book { title pages @include(if: false) } }", {"book": {"title": "Dune"}}),
        ("book", "{ book { title @skip(if: false) @include(if: true) } }", {"book": {"title": "Dune"}}),
        ("book", "{ book { title @cacheControl(maxAge: 30) } }", {"book": {"title": "Dune"}}),
        ("book", '{ book { title pages @skip(if: "yes") } }', {"book": {"title": "Dune", "pages": 1}}),
        ("book", "{ book { ...BookTitle } } fragment BookTitle on Book { title }", {"book": {"title": "Dune"}}),
        (
            "book",
            "{ book { ...A } } fragment A on Book { title ...B } fragment B on Book { ...A }",
            {"book": {"title": "Dune"}},
        ),
        (
            "items",
            "{ items { ... on Book { title } ... on Author { name } } }",
            {"items": [{"title": "Dune"}, {"name": "Herbert"}]},
        ),
        (
            "items",
            "{ items { ...BookTitle ...AuthorName } } fragment BookTitle on Book { title } "
            "fragment AuthorName on Author { name }",
            {"items": [{"title": "Dune"}, {"name": "Herbert"}]},
        ),
        (
            "items",
            "{ items { __typename ... on Book { title } } }",
            {"items": [{"__typename": "Book", "title": "Dune"}, {"__typename": "Author"}]},
        ),
        ("node", "{ node { id ... on Book { title } } }", {"node": {"id": "1", "title": "Dune"}}),
    ],
    ids=[
        "every-scalar-kind",
        "null-for-nullable-field",
        "integer-id",
        "whole-float-for-int",
        "aliases",
        "field-unknown-to-the-schema",
        "typename",
        "skipped-field",
        "excluded-field",
        "kept-field",
        "unrelated-directive",
        "malformed-condition",
        "fragment-spread",
        "cyclic-fragments",
        "union-inline-fragments",
        "union-fragment-spreads",
        "union-with-typename",
        "interface-with-inline-fragment",
    ],
)
def test_matching_data(ctx, field, query, data):
    _call(ctx, field, query, {"data": data})


@pytest.mark.parametrize(
    ("field", "query", "data", "message"),
    [
        ("book", "{ book { title } }", {"book": {"title": None}}, "`book.title`: null for non-nullable `String!`"),
        ("book", "{ book { title pages } }", {"book": {"title": "Dune"}}, "`book`: missing field `pages`"),
        (
            "book",
            "{ book { title pages } }",
            {"book": {}},
            "`book`: missing field `title`\n`book`: missing field `pages`",
        ),
        ("book", "{ book { title } }", {}, "`data`: missing field `book`"),
        (
            "book",
            "{ book { title } }",
            {"book": {"title": "Dune", "cursor": "abc"}},
            "`book`: unrequested field `cursor`",
        ),
        ("book", "{ book { pages } }", {"book": {"pages": "412"}}, "`book.pages`: expected `Int`, got string"),
        ("book", "{ book { pages } }", {"book": {"pages": True}}, "`book.pages`: expected `Int`, got boolean"),
        ("book", "{ book { pages } }", {"book": {"pages": 1.5}}, "`book.pages`: expected `Int`, got number"),
        (
            "book",
            "{ book { pages } }",
            {"book": {"pages": 2**31}},
            "`book.pages`: `2147483648` is outside the 32-bit range of `Int`",
        ),
        (
            "book",
            "{ book { genre } }",
            {"book": {"genre": "POETRY"}},
            "`book.genre`: `POETRY` is not a value of enum `Genre`",
        ),
        ("book", "{ book { tags } }", {"book": {"tags": "sf"}}, "`book.tags`: expected a list, got string"),
        (
            "book",
            "{ book { author { name } } }",
            {"book": {"author": ["x"]}},
            "`book.author`: expected an object, got array",
        ),
        (
            "book",
            "{ book { __typename } }",
            {"book": {"__typename": "Author"}},
            "`book.__typename`: expected `Book`, got `Author`",
        ),
        (
            "books",
            "{ books { tags } }",
            {"books": [{"tags": ["sf", None]}]},
            "`books[0].tags[1]`: null for non-nullable `String!`",
        ),
        (
            "books",
            "{ books { title } }",
            {"books": [{"title": None}, {"title": "Dune"}, {"title": None}, {"title": None}]},
            "`books[0].title`: null for non-nullable `String!` (and 2 more items)",
        ),
        (
            "items",
            "{ items { __typename } }",
            {"items": [{"__typename": "Car"}]},
            "`items[0].__typename`: `Car` is not a possible type of `Item`",
        ),
        (
            "items",
            "{ items { ... on Book { title } ... on Author { name } } }",
            {"items": [{"title": "Dune", "name": "Herbert"}]},
            "`items[0]`: matches no possible type of `Item`",
        ),
        (
            "items",
            "{ items { ... on Book { title } ... on Author { name } } }",
            {"items": [{"title": None}]},
            "`items[0].title`: null for non-nullable `String!`",
        ),
    ],
    ids=[
        "null-for-non-null-field",
        "missing-field",
        "several-missing-fields",
        "missing-root-field",
        "unrequested-field",
        "string-for-int",
        "boolean-for-int",
        "fraction-for-int",
        "int-out-of-range",
        "unknown-enum-value",
        "scalar-for-list",
        "list-for-object",
        "wrong-typename",
        "null-list-item",
        "same-defect-on-several-items",
        "impossible-typename",
        "union-matching-no-type",
        "union-member-with-a-wrong-field",
    ],
)
def test_mismatching_data(ctx, field, query, data, message):
    assert _failures(ctx, field, query, {"data": data}) == [(GraphQLDataMismatch, message)]


@pytest.mark.parametrize(
    "query",
    [
        "{ book { title",
        "query Q($skip: Boolean!) { book { title @skip(if: $skip) } }",
        "query A { book { title } } query B { book { title } }",
        "{ book { ... @defer { title } } }",
        "subscription { book { title } }",
    ],
    ids=["syntax-error", "variables", "several-operations", "deferred-fragment", "no-root-type"],
)
def test_data_of_queries_that_cannot_be_matched_is_not_checked(ctx, query):
    _call(ctx, "book", query, {"data": {"book": {}}})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "Response has neither `data` nor `errors`"),
        ({"data": []}, "`data`: expected an object, got array"),
    ],
    ids=["no-data", "array-data"],
)
def test_error_free_response_without_data_object(ctx, payload, message):
    assert _failures(ctx, "book", "{ book { title } }", payload) == [(GraphQLDataMismatch, message)]


def test_non_success_status_is_not_checked(ctx):
    _call(ctx, "book", "{ book { title } }", {"message": "Not Found"}, status_code=404)


def test_data_with_errors_is_not_checked_against_the_query(ctx):
    payload = {"data": {"book": {"title": None}}, "errors": [{"message": "Boom", "path": ["book"]}]}
    assert _failures(ctx, "book", "{ book { title } }", payload) == [(GraphQLServerError, "Boom")]


def test_response_without_a_case_is_checked_for_data_only(ctx):
    operation = ctx.graphql.load_sdl(SDL)["Query"]["book"]
    mismatching = ctx.graphql.apps.canned_response({"data": {"book": {"cursor": "abc"}}})
    operation.validate_response(requests.post(mismatching.base_url, json={"query": "{ book { title } }"}))
    empty = ctx.graphql.apps.canned_response({})
    with pytest.raises(GraphQLDataMismatch) as exc:
        operation.validate_response(requests.post(empty.base_url, json={"query": "{ book { title } }"}))
    assert exc.value.message == "Response has neither `data` nor `errors`"


def test_mismatches_on_any_number_of_items_are_one_failure(ctx):
    failures = []
    for data in (
        {"books": [{"title": None}]},
        {"books": [{"title": "Dune"}, {"title": None}]},
        {"books": [{"title": None}, {"title": None}]},
    ):
        with pytest.raises(FailureGroup) as exc:
            _call(ctx, "books", "{ books { title } }", {"data": data})
        failures.extend(exc.value.exceptions)
    assert failures[0] == failures[1] == failures[2]


# Each level is checked against every implementation, so a walk that repeats work never finishes.
@pytest.mark.parametrize(
    ("query", "data", "expected"),
    [
        (_nested_query(f"id ... on T{LAST} {{ f{LAST} }}"), _nested_data({"id": "1"}, {f"f{LAST}": 1}), []),
        (
            _nested_query("id"),
            _nested_data({"id": None}, {}),
            [(GraphQLDataMismatch, f"`node{'.child' * (DEPTH - 1)}.id`: null for non-nullable `ID!`")],
        ),
    ],
    ids=["matching", "deep-mismatch"],
)
def test_deeply_nested_interfaces_without_typename(ctx, query, data, expected):
    if expected:
        assert _failures(ctx, "node", query, {"data": data}, sdl=NESTED_SDL) == expected
    else:
        _call(ctx, "node", query, {"data": data}, sdl=NESTED_SDL)
