from __future__ import annotations

import random

import graphql
import pytest

from schemathesis.specs.graphql.handles import Handle
from schemathesis.specs.graphql.inference import CLEANUP_PREFIXES, OperationRole, RootType, extract_entity
from schemathesis.specs.graphql.stateful._rules import _inject_field, _OperationSummary


def _operation(query: str) -> graphql.OperationDefinitionNode:
    node = graphql.parse(query).definitions[0]
    assert isinstance(node, graphql.OperationDefinitionNode)
    return node


def test_inject_field_adds_missing_field():
    operation = _operation("query { projects { id } }")
    operation = _inject_field("projects", ("fullPath",))(operation, random.Random(0))
    assert "fullPath" in graphql.print_ast(operation)


def test_inject_field_idempotent_when_present():
    operation = _operation("query { projects { fullPath } }")
    operation = _inject_field("projects", ("fullPath",))(operation, random.Random(0))
    assert graphql.print_ast(operation).count("fullPath") == 1


def test_inject_field_through_relay_connection():
    operation = _operation("query { products { totalCount } }")
    operation = _inject_field("products", ("edges", "node", "slug"))(operation, random.Random(0))
    printed = graphql.print_ast(operation)
    assert "edges" in printed and "node" in printed and "slug" in printed


def test_inject_field_reuses_existing_edges_node():
    operation = _operation("query { products { edges { node { id } } } }")
    operation = _inject_field("products", ("edges", "node", "slug"))(operation, random.Random(0))
    printed = graphql.print_ast(operation)
    assert printed.count("edges") == 1 and printed.count("node") == 1 and "slug" in printed


def test_inject_field_through_mutation_payload():
    operation = _operation("mutation { createNote { clientMutationId } }")
    operation = _inject_field("createNote", ("note", "id"))(operation, random.Random(0))
    assert (
        graphql.print_ast(operation)
        == "mutation {\n  createNote {\n    clientMutationId\n    note {\n      id\n    }\n  }\n}"
    )


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
        ("deleteBook", "Book"),
        ("bookDelete", "Book"),
        ("removePost", "Post"),
        ("postRemove", "Post"),
        ("book", None),
        ("addBook", None),
        ("delete", None),
    ],
    ids=[
        "verb-first-camel",
        "verb-last-camel",
        "verb-first-camel-alt",
        "verb-last-camel-alt",
        "single-token",
        "non-cleanup-prefix",
        "verb-only",
    ],
)
def test_extract_entity(field_name, expected):
    assert extract_entity(field_name, prefixes=CLEANUP_PREFIXES) == expected
