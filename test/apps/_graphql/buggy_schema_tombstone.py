"""GraphQL schema with addBook/deleteBook/updateBook chain; tombstoning prevents the pool from re-feeding deleted ids to updateBook."""

from __future__ import annotations

import uuid
from typing import NewType

import strawberry
from strawberry.schema.config import StrawberryConfig
from strawberry.types.scalar import ScalarDefinition

BookID = NewType("BookID", str)


@strawberry.type
class Book:
    id: BookID
    title: str


BUGGY_BOOKS: dict[str, Book] = {}


@strawberry.type
class Query:
    @strawberry.field
    def book(self, id: BookID) -> Book | None:
        return BUGGY_BOOKS.get(id)


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addBook(self, title: str) -> Book:
        new_id = BookID(str(uuid.uuid4()))
        book = Book(id=new_id, title=title)
        BUGGY_BOOKS[new_id] = book
        return book

    @strawberry.mutation
    def deleteBook(self, id: BookID) -> bool:
        BUGGY_BOOKS.pop(id, None)
        return True

    @strawberry.mutation
    def updateBook(self, id: BookID, title: str) -> Book | None:
        if id in BUGGY_BOOKS and title:
            raise RuntimeError("planted: update-on-existing")
        return None


schema = strawberry.Schema(
    Query,
    Mutation,
    config=StrawberryConfig(
        scalar_map={
            BookID: ScalarDefinition(
                name="BookID",
                description=None,
                specified_by_url=None,
                serialize=lambda v: v,
                parse_value=lambda v: v,
                parse_literal=None,
            ),
        },
    ),
)
