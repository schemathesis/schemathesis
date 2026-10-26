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

_MULTI_WORD_RESOURCE = """
    type Cover { id: ID! }
    type BookCover { id: ID! }
    type DeleteBookCover { id: ID! ok: Boolean }
    type Query { _: Boolean }
    type Mutation {
        addCover(url: String!): Cover!
        addBookCover(url: String!): BookCover!
        deleteBookCover(id: ID!): Boolean
        bookCoverDelete(id: ID!): Boolean
        removeBookCover(id: ID!): DeleteBookCover
    }
"""

_QUALIFIED_CLEANUP = """
    type Session { id: ID! }
    type Query { _: Boolean }
    type Mutation {
        addSession(name: String!): Session!
        deleteUserSession(id: ID!): Boolean
    }
"""

_DELETE_PAYLOAD_WITH_RELATED_RECORD = """
    type Post { id: ID! }
    type Comment { id: ID! }
    type User { id: ID! }
    type DeleteCommentPayload { deletedCommentId: ID, post: Post }
    type RemoveCommentPayload { ok: Boolean, viewer: User }
    type Query { _: Boolean }
    type Mutation {
        addPost(title: String!): Post!
        addComment(text: String!): Comment!
        addUser(name: String!): User!
        deleteComment(id: ID!): DeleteCommentPayload
        removeComment(id: ID!): RemoveCommentPayload
    }
"""

_MULTI_WORD_ENVELOPE = """
    type Comment { id: ID! }
    type BlogComment { id: ID! }
    type DeleteBlogComment { id: ID! ok: Boolean }
    type Query { _: Boolean }
    type Mutation {
        addComment(text: String!): Comment!
        addBlogComment(text: String!): BlogComment!
        deleteBlogComment(id: ID!): DeleteBlogComment
    }
"""

_UPDATE_ENVELOPE = """
    type Note { id: ID! title: String }
    type UpdateNote { id: ID! ok: Boolean }
    type Query { note(id: ID!): Note }
    type Mutation {
        createNote(title: String): Note
        updateNote(id: ID!, title: String): UpdateNote
    }
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
        (
            _MULTI_WORD_RESOURCE,
            [
                ("Mutation.addBookCover", "Mutation.deleteBookCover"),
                ("Mutation.addBookCover", "Mutation.bookCoverDelete"),
                ("Mutation.addBookCover", "Mutation.removeBookCover"),
            ],
        ),
        (_QUALIFIED_CLEANUP, [("Mutation.addSession", "Mutation.deleteUserSession")]),
        (
            _DELETE_PAYLOAD_WITH_RELATED_RECORD,
            [("Mutation.addComment", "Mutation.deleteComment"), ("Mutation.addComment", "Mutation.removeComment")],
        ),
        (_MULTI_WORD_ENVELOPE, [("Mutation.addBlogComment", "Mutation.deleteBlogComment")]),
        (
            _UPDATE_ENVELOPE,
            [("Mutation.createNote", "Query.note"), ("Mutation.createNote", "Mutation.updateNote")],
        ),
        (_NO_PRODUCER, []),
    ],
    ids=[
        "producer-to-reader",
        "no-match-different-types",
        "cleanup-is-not-producer",
        "envelope-result-binds-to-resource",
        "verb-prefixed-resource-unaffected",
        "multi-word-resource-name",
        "qualified-cleanup-falls-back-to-head-noun",
        "delete-payload-with-related-record",
        "multi-word-envelope",
        "update-envelope-binds-to-resource",
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
