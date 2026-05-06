from __future__ import annotations

import hypothesis
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine

import schemathesis
from schemathesis.core.errors import NoProducers
from schemathesis.core.failures import FailureGroup
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS
from schemathesis.specs.graphql.stateful import GraphQLStateMachine, create_state_machine


@pytest.fixture
def _register_book_id_scalar():
    schemathesis.graphql.scalar("BookID", st.uuids().map(str).map(nodes.String))
    yield
    CUSTOM_SCALARS.clear()


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
            type Query { _: Int }
            type Mutation {
                addBook(title: String!): Book!
                addAuthor(name: String!): Author!
                removeBookFromAuthor(bookId: ID!, authorId: ID!): Boolean
            }
        """)
    )
    assert "Mutation_removeBookFromAuthor" in cls.__dict__
    assert "Mutation_removeBookFromAuthor_double" in cls.__dict__


def test_init_raises_NoProducers_when_no_rules(ctx):
    cls = create_state_machine(
        ctx.graphql.load_sdl("""
            type Book { id: ID! }
            type Query { book(id: ID!): Book }
            type Mutation { _: Boolean }
        """)
    )
    with pytest.raises(NoProducers, match="producer"):
        cls()


@pytest.mark.parametrize(
    "method_name",
    ["_add_result_to_targets", "_add_results_to_targets"],
    ids=["single", "bulk"],
)
def test_routing_override_present(method_name):
    # Regression: rule-declared target= must populate its bundle (overrides the parent's filter).
    assert method_name in GraphQLStateMachine.__dict__


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


def test_state_machine_finds_planted_bug_via_python_api(_register_book_id_scalar, ctx):
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
