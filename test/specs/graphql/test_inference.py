from __future__ import annotations

import enum

import pytest

from schemathesis.specs.graphql.inference import OperationRole, RootType, classify_operation


@pytest.mark.parametrize(
    ("field_name", "root_type", "expected"),
    [
        ("getBook", RootType.QUERY, OperationRole.READER),
        ("listBooks", RootType.QUERY, OperationRole.READER),
        ("books", RootType.QUERY, OperationRole.READER),
        ("createBook", RootType.MUTATION, OperationRole.PRODUCER),
        ("addBook", RootType.MUTATION, OperationRole.PRODUCER),
        ("registerAuthor", RootType.MUTATION, OperationRole.PRODUCER),
        ("newAccount", RootType.MUTATION, OperationRole.PRODUCER),
        ("insertRow", RootType.MUTATION, OperationRole.PRODUCER),
        ("updateBook", RootType.MUTATION, OperationRole.MUTATOR),
        ("setTitle", RootType.MUTATION, OperationRole.MUTATOR),
        ("replaceBook", RootType.MUTATION, OperationRole.MUTATOR),
        ("patchBook", RootType.MUTATION, OperationRole.MUTATOR),
        ("editBook", RootType.MUTATION, OperationRole.MUTATOR),
        ("deleteBook", RootType.MUTATION, OperationRole.CLEANUP),
        ("removeBook", RootType.MUTATION, OperationRole.CLEANUP),
        ("destroyBook", RootType.MUTATION, OperationRole.CLEANUP),
        ("purgeCache", RootType.MUTATION, OperationRole.CLEANUP),
        ("publishPost", RootType.MUTATION, OperationRole.MUTATOR),
        ("CREATEbook", RootType.MUTATION, OperationRole.PRODUCER),
        ("addressUpdate", RootType.MUTATION, OperationRole.MUTATOR),
        ("destroyerInit", RootType.MUTATION, OperationRole.MUTATOR),
        ("makeReservation", RootType.MUTATION, OperationRole.PRODUCER),
        ("cloneRepository", RootType.MUTATION, OperationRole.PRODUCER),
        ("importData", RootType.MUTATION, OperationRole.PRODUCER),
        ("uploadFile", RootType.MUTATION, OperationRole.PRODUCER),
        ("clearCache", RootType.MUTATION, OperationRole.CLEANUP),
        ("revokeToken", RootType.MUTATION, OperationRole.CLEANUP),
        ("productCreate", RootType.MUTATION, OperationRole.PRODUCER),
        ("customerUpdate", RootType.MUTATION, OperationRole.MUTATOR),
        ("orderDelete", RootType.MUTATION, OperationRole.CLEANUP),
        ("subscribe", RootType.MUTATION, OperationRole.MUTATOR),
    ],
    ids=lambda v: v.name if isinstance(v, enum.Enum) else str(v),
)
def test_classify(field_name: str, root_type: RootType, expected: OperationRole) -> None:
    assert classify_operation(field_name=field_name, root_type=root_type) == expected
