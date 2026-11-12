from __future__ import annotations

import random

import graphql
import pytest

from schemathesis.specs.graphql.handles import Handle
from schemathesis.specs.graphql.inference import CLEANUP_PREFIXES, OperationRole, RootType, entity_candidates
from schemathesis.specs.graphql.stateful._rules import _inject_field, _OperationSummary


def _operation(query: str) -> graphql.OperationDefinitionNode:
    node = graphql.parse(query).definitions[0]
    assert isinstance(node, graphql.OperationDefinitionNode)
    return node


@pytest.mark.parametrize(
    ("query", "field_name", "path", "expected"),
    [
        (
            "query { other { x } projects { id } }",
            "projects",
            ("fullPath",),
            "query { other { x } projects { id fullPath } }",
        ),
        ("query { projects { fullPath } }", "projects", ("fullPath",), "query { projects { fullPath } }"),
        (
            "query { products { totalCount } }",
            "products",
            ("edges", "node", "slug"),
            "query { products { totalCount edges { node { slug } } } }",
        ),
        (
            "query { products { edges { node { id } } } }",
            "products",
            ("edges", "node", "slug"),
            "query { products { edges { node { id slug } } } }",
        ),
        (
            "mutation { createNote { clientMutationId } }",
            "createNote",
            ("note", "id"),
            "mutation { createNote { clientMutationId note { id } } }",
        ),
    ],
    ids=[
        "adds-missing-field",
        "idempotent-when-present",
        "through-relay-connection",
        "reuses-edges-node",
        "through-payload",
    ],
)
def test_inject_field(query, field_name, path, expected):
    operation = _inject_field(field_name, path)(_operation(query), random.Random(0))
    assert graphql.print_ast(operation) == graphql.print_ast(_operation(expected))


def test_operation_summaries_unwrap_mutation_payloads(ctx):
    schema = ctx.graphql.load_sdl("""
        type Note { id: ID!, title: String }
        type CreateNotePayload { note: Note, clientMutationId: String }
        type UpdateNotePayload { note: Note }
        type DeleteNotePayload { deletedNoteId: ID, clientMutationId: String }
        input NoteInput { title: String }
        type Query { note(id: ID!): Note }
        type Mutation {
            createNote(input: NoteInput): CreateNotePayload
            updateNote(id: ID!, input: NoteInput): UpdateNotePayload
            deleteNote(id: ID!): DeleteNotePayload
        }
    """)
    note_id = Handle("Note", "id")
    assert schema.analysis.summaries == [
        _OperationSummary(
            label="Query.note",
            role=OperationRole.READER,
            return_type_name="Note",
            consumed={"id": note_id},
            field_name="note",
            root_type=RootType.QUERY,
            unwrapped_return_name="Note",
            record_path=(),
        ),
        _OperationSummary(
            label="Mutation.createNote",
            role=OperationRole.PRODUCER,
            return_type_name="Note",
            consumed={},
            field_name="createNote",
            root_type=RootType.MUTATION,
            unwrapped_return_name="Note",
            record_path=("note",),
        ),
        _OperationSummary(
            label="Mutation.updateNote",
            role=OperationRole.MUTATOR,
            return_type_name="Note",
            consumed={"id": note_id},
            field_name="updateNote",
            root_type=RootType.MUTATION,
            unwrapped_return_name="Note",
            record_path=("note",),
        ),
        _OperationSummary(
            label="Mutation.deleteNote",
            role=OperationRole.CLEANUP,
            return_type_name=None,
            consumed={"id": note_id},
            field_name="deleteNote",
            root_type=RootType.MUTATION,
            unwrapped_return_name="DeleteNotePayload",
            record_path=(),
        ),
    ]


@pytest.mark.parametrize(
    ("field_name", "expected"),
    [
        ("deleteBook", ("Book",)),
        ("bookDelete", ("Book",)),
        ("removePost", ("Post",)),
        ("postRemove", ("Post",)),
        ("deleteBookCover", ("BookCover", "Cover")),
        ("bookCoverDelete", ("BookCover", "Cover")),
        ("book", ()),
        ("addBook", ()),
        ("delete", ()),
        ("_deleteBook", ()),
    ],
    ids=[
        "verb-first-camel",
        "verb-last-camel",
        "verb-first-camel-alt",
        "verb-last-camel-alt",
        "multi-word-verb-first",
        "multi-word-verb-last",
        "single-token",
        "non-cleanup-prefix",
        "verb-only",
        "leading-underscore",
    ],
)
def test_entity_candidates(field_name, expected):
    assert entity_candidates(field_name, prefixes=CLEANUP_PREFIXES) == expected
