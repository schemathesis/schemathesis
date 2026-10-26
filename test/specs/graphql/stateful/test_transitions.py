from __future__ import annotations

import pytest

from schemathesis.specs.graphql.handles import Handle

_PRODUCER_TO_READER = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation { addBook(title: String!): Book! }
"""

_DIFFERENT_TYPES = """
    type Book { id: ID! }
    type Author { id: ID! }
    type Query { author(id: ID!): Author }
    type Mutation { addBook(title: String!): Book! }
"""

_PRODUCER_PLUS_CLEANUP = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation {
        addBook(title: String!): Book!
        deleteBook(id: ID!): Boolean
    }
"""

_ENVELOPE_RESULT = """
    type Note { id: ID! title: String }
    type DeleteNote { id: ID! ok: Boolean }
    type Query { note(id: ID!): Note }
    type Mutation {
        createNote(title: String): Note
        removeNote(id: ID!): DeleteNote
    }
"""

_VERB_PREFIXED_RESOURCE = """
    type CloneSession { id: ID! }
    type Query { cloneSession(id: ID!): CloneSession }
    type Mutation { addCloneSession(name: String!): CloneSession! }
"""

_READABLE_VERB_PREFIXED_RESOURCE = """
    type Job { id: ID! }
    type ImportJob { id: ID! }
    type Query { importJob(id: ID!): ImportJob }
    type Mutation { startImportJob(source: String!): ImportJob }
"""

_NO_PRODUCER = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation { _: Boolean }
"""


@pytest.mark.parametrize(
    ("sdl", "expected_edges"),
    [
        (_PRODUCER_TO_READER, [("Mutation.addBook", "Query.book")]),
        (_DIFFERENT_TYPES, []),
        (
            _PRODUCER_PLUS_CLEANUP,
            [("Mutation.addBook", "Query.book"), ("Mutation.addBook", "Mutation.deleteBook")],
        ),
        (
            _ENVELOPE_RESULT,
            [("Mutation.createNote", "Query.note"), ("Mutation.createNote", "Mutation.removeNote")],
        ),
        (_VERB_PREFIXED_RESOURCE, [("Mutation.addCloneSession", "Query.cloneSession")]),
        (_NO_PRODUCER, []),
    ],
    ids=[
        "producer-to-reader",
        "no-match-different-types",
        "cleanup-is-not-producer",
        "envelope-result-binds-to-resource",
        "verb-prefixed-resource-unaffected",
        "no-producer-no-edges",
    ],
)
def test_transition_edges(ctx, sdl, expected_edges):
    transitions = ctx.graphql.load_sdl(sdl).analysis.transitions
    edges = [(label, edge.target.label) for label, ops in transitions.operations.items() for edge in ops.outgoing]
    assert sorted(edges) == sorted(expected_edges)


def test_transition_count_is_stable_across_reads(ctx):
    analysis = ctx.graphql.load_sdl(_PRODUCER_TO_READER).analysis

    assert [analysis.transition_count, analysis.transition_count] == [1, 1]


def test_outgoing_and_incoming_are_consistent(ctx):
    # For each outgoing edge from operation A, operation B should list it as incoming.
    transitions = ctx.graphql.load_sdl(_PRODUCER_TO_READER).analysis.transitions
    for source, ops in transitions.operations.items():
        for edge in ops.outgoing:
            target_incoming = transitions.operations[edge.target.label].incoming
            assert any(
                incoming.source.label == source and incoming.target.label == edge.target.label
                for incoming in target_incoming
            )


def test_readable_verb_prefixed_resource_keeps_its_id_handle(ctx):
    analysis = ctx.graphql.load_sdl(_READABLE_VERB_PREFIXED_RESOURCE).analysis

    assert Handle("ImportJob", "id") in analysis.handles
    assert {summary.label: summary.consumed for summary in analysis.summaries} == {
        "Query.importJob": {"id": Handle("ImportJob", "id")},
        "Mutation.startImportJob": {},
    }
