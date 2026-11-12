from __future__ import annotations

import subprocess
import sys
import uuid

import graphql
import hypothesis
import pytest
import strawberry
from hypothesis.stateful import RuleBasedStateMachine

import schemathesis
from schemathesis.core import INTERNAL_BUFFER_SIZE
from schemathesis.core.errors import NoProducers
from schemathesis.core.failures import FailureGroup
from schemathesis.generation.meta import TestPhase
from schemathesis.generation.stateful.state_machine import BundleStateMachine
from schemathesis.specs.graphql.stateful import GraphQLStateMachine, create_state_machine
from test.apps.catalog.graphql import bookstore
from test.utils import saved_example_keys

_FULL_SCHEMA_SDL = """
    type Book { id: ID! }
    type Author { id: ID! }
    type Query { book(id: ID!): Book author(id: ID!): Author }
    type Mutation {
        addBook(title: String!): Book!
        addAuthor(name: String!): Author!
        deleteBook(id: ID!): Boolean
    }
"""


@pytest.fixture
def full_state_machine(ctx) -> type[GraphQLStateMachine]:
    return create_state_machine(ctx.graphql.load_sdl(_FULL_SCHEMA_SDL))


def test_non_id_cleanup_does_not_require_deleted_bundle(ctx):
    # A delete op keyed on a non-id handle has no deleted bundle; building the machine must not demand one.
    machine = create_state_machine(
        ctx.graphql.load_sdl("""
            type Project { fullPath: String! }
            type Query { projects: [Project!]! }
            type Mutation {
                moveIssue(projectPath: String!, title: String!): Boolean
                deleteProject(projectPath: String!): Boolean
            }
        """)
    )
    assert issubclass(machine, GraphQLStateMachine)


def test_create_state_machine_returns_a_subclass(ctx):
    cls = create_state_machine(
        ctx.graphql.load_sdl("""
            type Book { id: ID! }
            type Query { book(id: ID!): Book }
            type Mutation { addBook(title: String!): Book! }
        """)
    )
    assert issubclass(cls, GraphQLStateMachine)
    assert issubclass(cls, RuleBasedStateMachine)


def test_repeated_as_state_machine_builds_same_rules(ctx):
    schema = ctx.graphql.load_sdl("""
        type Book { id: ID! }
        type Query { book(id: ID!): Book }
        type Mutation { addBook(title: String!): Book! }
    """)
    first = schema.as_state_machine()
    second = schema.as_state_machine()
    rules = [
        sorted(name for name, value in machine.__dict__.items() if hasattr(value, "hypothesis_stateful_rule"))
        for machine in (first, second)
    ]
    assert first is not second
    assert rules == [["Mutation_addBook", "Query_book"], ["Mutation_addBook", "Query_book"]]


# Generated queries need more room than Hypothesis allows by default, even when no other Schemathesis test ran first.
_BUFFER_PROBE = (
    "import schemathesis; "
    "from hypothesis.internal.conjecture import engine; "
    "schemathesis.graphql.from_file('type Book { id: ID! } type Query { book(id: ID!): Book }').as_state_machine(); "
    "print(engine.BUFFER_SIZE)"
)


def test_as_state_machine_raises_hypothesis_buffer_size():
    output = subprocess.run([sys.executable, "-c", _BUFFER_PROBE], capture_output=True, text=True, check=True).stdout
    assert output.strip() == str(INTERNAL_BUFFER_SIZE)


@pytest.mark.parametrize(
    "bundle_name",
    ["Book_ids", "Author_ids", "deleted_Book_ids", "deleted_Author_ids"],
    ids=["alive-Book", "alive-Author", "deleted-Book", "deleted-Author"],
)
def test_state_machine_has_per_type_bundle(full_state_machine, bundle_name):
    assert bundle_name in full_state_machine.__dict__


@pytest.mark.parametrize(
    "rule_name",
    [
        "Mutation_addBook",
        "Mutation_addAuthor",
        "Query_book",
        "Query_author",
        "Mutation_deleteBook",
        "Mutation_deleteBook_double",
        "Query_book_on_deleted",
    ],
    ids=[
        "producer-Book",
        "producer-Author",
        "consumer-Book",
        "consumer-Author",
        "cleanup-Book",
        "double-cleanup-probe-Book",
        "use-after-delete-probe-Book",
    ],
)
def test_state_machine_generates_rule(full_state_machine, rule_name):
    assert rule_name in full_state_machine.__dict__


def test_use_after_delete_probe_only_for_types_with_cleanup(full_state_machine):
    # Author has no cleanup mutation, so Query.author should not get a use-after-delete probe.
    assert "Query_author_on_deleted" not in full_state_machine.__dict__


def test_cleanup_with_multiple_id_args_wires_secondary_bundles(ctx):
    cls = create_state_machine(
        ctx.graphql.load_sdl("""
            type Book { id: ID! }
            type Author { id: ID! }
            type Query { pair(bookId: ID!, authorId: ID!): Boolean }
            type Mutation {
                addBook(title: String!): Book!
                addAuthor(name: String!): Author!
                removeBookFromAuthor(bookId: ID!, authorId: ID!): Boolean
            }
        """)
    )
    assert {
        name: {
            argument: (bundle.name, bundle.consume)
            for argument, bundle in cls.__dict__[name].hypothesis_stateful_rule.arguments.items()
            if argument != "data"
        }
        for name in ("Mutation_removeBookFromAuthor", "Mutation_removeBookFromAuthor_double", "Query_pair_on_deleted")
    } == {
        "Mutation_removeBookFromAuthor": {"bookId": ("Book_ids", True), "authorId": ("Author_ids", False)},
        "Mutation_removeBookFromAuthor_double": {
            "bookId": ("deleted_Book_ids", True),
            "authorId": ("Author_ids", False),
        },
        "Query_pair_on_deleted": {"bookId": ("deleted_Book_ids", False), "authorId": ("Author_ids", False)},
    }


@pytest.mark.parametrize(
    ("sdl", "expected"),
    [
        (
            """
            type Book { id: ID! isbnCode: String! title: String! }
            type Query { books: [Book!]! }
            type Mutation { addBook(title: String!): Book! tagBook(bookPrimaryIsbnCode: String!): Boolean }
            """,
            [
                "Book__isbnCode",
                "Mutation_addBook",
                "Mutation_addBook__isbnCode",
                "Mutation_tagBook",
                "Query_books__isbnCode",
            ],
        ),
        (
            """
            type Book { id: ID! slug: String! urlSlug: String! pathSlug: String! shortSlug: String! oldSlug: String! }
            type Query { books: [Book!]! book(slug: String!): Book }
            type Mutation { addBook(title: String!): Book! }
            """,
            [
                "Book__slug",
                "Mutation_addBook",
                "Mutation_addBook__slug",
                "Query_book",
                "Query_book__slug",
                "Query_books__slug",
            ],
        ),
        (
            """
            type Book { id: ID! slug: String! urlSlug: String! pathSlug: String! shortSlug: String! oldSlug: String! }
            type Query { books: [Book!]! }
            type Mutation { addBook(title: String!): Book! tagBook(bookSlug: String!): Boolean }
            """,
            ["Book__slug", "Mutation_addBook", "Mutation_addBook__slug", "Mutation_tagBook", "Query_books__slug"],
        ),
        (
            """
            type Book { id: ID! title: String! }
            type Query { books: [Book!]! book(code: String!): Book }
            type Mutation { addBook(title: String!): Book! }
            """,
            ["Mutation_addBook"],
        ),
    ],
    ids=[
        "qualified-argument-ends-with-field",
        "bare-argument-prefers-exact-field",
        "typed-argument-prefers-exact-field",
        "bare-argument-without-matching-field",
    ],
)
def test_identifier_argument_bound_to_producer_field(ctx, sdl, expected):
    cls = ctx.graphql.load_sdl(sdl).as_state_machine()
    common = {"Book_ids", "deleted_Book_ids", "schema"}
    assert sorted(name for name in cls.__dict__ if not name.startswith("_") and name not in common) == expected


def test_operation_returning_other_type_does_not_produce_foreign_handle(ctx):
    sdl = """
    type Book { id: ID! slug: String! title: String! }
    type Author { id: ID! name: String! }
    type Query { books: [Book!]! authors: [Author!]! book(slug: String!): Book }
    type Mutation { addBook(title: String!): Book! }
    """
    cls = ctx.graphql.load_sdl(sdl).as_state_machine()
    common = {"Book_ids", "deleted_Book_ids", "schema"}
    assert sorted(name for name in cls.__dict__ if not name.startswith("_") and name not in common) == [
        "Author_ids",
        "Book__slug",
        "Mutation_addBook",
        "Mutation_addBook__slug",
        "Query_book",
        "Query_book__slug",
        "Query_books__slug",
        "deleted_Author_ids",
    ]


def test_init_raises_NoProducers_when_no_rules(ctx):
    cls = create_state_machine(
        ctx.graphql.load_sdl("""
            type Book { id: ID! }
            type Query { book(id: ID!): Book }
            type Mutation { _: Boolean }
        """)
    )
    with pytest.raises(NoProducers) as exc_info:
        cls()
    assert str(exc_info.value) == (
        "GraphQL stateful testing requires at least one producer mutation "
        "(a mutation returning an Object type with an `id` field)."
    )


@pytest.mark.parametrize(
    "method_name",
    ["_add_result_to_targets", "_add_results_to_targets"],
    ids=["single", "bulk"],
)
def test_routing_override_present(method_name):
    # Regression: rule-declared target= must populate its bundle (overrides the parent's filter).
    assert method_name in BundleStateMachine.__dict__


def test_state_machine_has_real_transition_controller(ctx):
    schema = ctx.graphql.load_sdl("""
        type Book { id: ID! }
        type Query { book(id: ID!): Book }
        type Mutation { addBook(title: String!): Book! }
    """)
    instance = create_state_machine(schema)()
    # Real transitions, not the empty placeholder.
    assert "Mutation.addBook" in instance.control.transitions.operations
    add_outgoing = [edge.target.label for edge in instance.control.transitions.operations["Mutation.addBook"].outgoing]
    assert "Query.book" in add_outgoing


def test_state_machine_finds_planted_bug_via_python_api(book_id_scalar, ctx):
    # The CLI path wraps validate_response with its own version; `.run()` is the only path
    # that exercises the state-machine override.
    api = ctx.graphql.apps.use_after_create()
    schema = schemathesis.graphql.from_url(api.schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])
    StateMachine = schema.as_state_machine()

    with pytest.raises(FailureGroup):
        StateMachine.run(
            settings=hypothesis.settings(
                max_examples=20,
                deadline=None,
                database=None,
                phases=[hypothesis.Phase.generate],
                suppress_health_check=list(hypothesis.HealthCheck),
            )
        )


def test_saved_scenarios_are_kept_per_schema(ctx):
    use_after_create = ctx.graphql.apps.use_after_create().schema_url
    keys = saved_example_keys(schemathesis.graphql.from_url(use_after_create).as_state_machine())
    assert keys == saved_example_keys(schemathesis.graphql.from_url(use_after_create).as_state_machine())
    use_after_delete = ctx.graphql.serve(bookstore.use_after_delete()).schema_url
    assert keys.isdisjoint(saved_example_keys(schemathesis.graphql.from_url(use_after_delete).as_state_machine()))


@strawberry.type
class _Book:
    id: strawberry.ID
    title: str


@strawberry.type
class _Project:
    id: strawberry.ID
    fullPath: str


def _crud_with_non_id_handle() -> strawberry.Schema:
    books: set[str] = set()

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: strawberry.ID) -> _Book | None:
            return _Book(id=id, title="t") if id in books else None

        @strawberry.field
        def projects(self) -> list[_Project]:
            return [_Project(id=strawberry.ID("1"), fullPath="acme/web")]

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> _Book:
            book_id = str(uuid.uuid4())
            books.add(book_id)
            return _Book(id=strawberry.ID(book_id), title=title)

        @strawberry.mutation
        def deleteBook(self, id: strawberry.ID) -> bool:
            books.discard(id)
            return True

        @strawberry.mutation
        def moveIssue(self, projectPath: str) -> bool:
            return False

    return strawberry.Schema(Query, Mutation)


def _selected_fields(case):
    field = graphql.parse(case.body).definitions[0].selection_set.selections[0]
    return {selection.name.value for selection in field.selection_set.selections}


def test_every_rule_kind_records_its_parent_step_and_applies_hooks(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.from_schema(_crud_with_non_id_handle()).schema_url)
    schema.config.checks.update(included_check_names=["not_a_server_error"])

    @schema.hook
    def map_headers(context, headers):
        return {"X-Hook": "1"}

    captured_fields = {"Mutation.addBook": "id", "Query.projects": "fullPath"}
    steps = set()

    class Recording(schema.as_state_machine()):
        def after_call(self, response, case):
            super().after_call(response, case)
            node = self.recorder.cases[case.id]
            transition = node.transition
            if transition is None:
                step = (captured_fields[case.operation.label] in _selected_fields(case),)
            else:
                parent = self.recorder.cases[node.parent_id].value
                derived = self.control.statistic.get(parent.operation.label, {}).get(parent.id, {})
                step = (
                    transition.id,
                    parent.operation.label,
                    transition.parent_id == node.parent_id,
                    transition.is_inferred,
                    derived.get(case.operation.label, 0) > 0,
                )
            steps.add(
                (case.operation.label, step, node.is_transition_applied, case.headers["X-Hook"], case.meta.phase.name)
            )

    Recording.run(
        settings=hypothesis.settings(
            max_examples=10,
            deadline=None,
            database=None,
            derandomize=True,
            phases=[hypothesis.Phase.generate],
            suppress_health_check=list(hypothesis.HealthCheck),
        )
    )
    assert steps == {
        ("Mutation.addBook", (True,), False, "1", TestPhase.STATEFUL),
        ("Query.projects", (True,), False, "1", TestPhase.STATEFUL),
        ("Query.book", ("Query_book", "Mutation.addBook", True, False, True), True, "1", TestPhase.STATEFUL),
        (
            "Mutation.deleteBook",
            ("Mutation_deleteBook", "Mutation.addBook", True, False, True),
            True,
            "1",
            TestPhase.STATEFUL,
        ),
        (
            "Query.book",
            ("Query_book_on_deleted", "Mutation.deleteBook", True, False, False),
            True,
            "1",
            TestPhase.STATEFUL,
        ),
        (
            "Mutation.deleteBook",
            ("Mutation_deleteBook_double", "Mutation.deleteBook", True, False, False),
            True,
            "1",
            TestPhase.STATEFUL,
        ),
        (
            "Mutation.moveIssue",
            ("Mutation_moveIssue", "Query.projects", True, False, True),
            True,
            "1",
            TestPhase.STATEFUL,
        ),
    }


def test_rules_cover_every_handle_kind_and_skip_unseedable_consumers(ctx):
    cls = create_state_machine(
        ctx.graphql.load_sdl("""
            type Book { id: ID! }
            type Project { id: ID! fullPath: String! }
            type Repo { id: ID! slug: String! }
            type Query { repo(slug: String!): Repo }
            type Mutation {
                addBook(title: String!): Book!
                createProject(name: String!): Project!
                moveIssue(projectPath: String!): Boolean
                starRepo(repoSlug: String!): Boolean
            }
        """)
    )
    assert sorted(name for name in cls.__dict__ if name.startswith(("Query_", "Mutation_"))) == [
        "Mutation_addBook",
        "Mutation_createProject",
        "Mutation_createProject__fullPath",
        "Mutation_moveIssue",
    ]
