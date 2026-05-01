"""GraphQL schema with bugs gated on real ids; only the resource pool can reach them."""

from __future__ import annotations

import uuid
from typing import NewType

import strawberry

BookID = strawberry.scalar(
    NewType("BookID", str),
    serialize=lambda v: v,
    parse_value=lambda v: v,
)


@strawberry.type
class Book:
    id: BookID
    title: str


BUGGY_BOOKS: dict[str, Book] = {}


@strawberry.type
class Query:
    @strawberry.field
    def book(self, id: BookID) -> Book | None:
        if id in BUGGY_BOOKS:
            raise RuntimeError("planted: use-after-create")
        return None


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addBook(self, title: str) -> Book:
        new_id = BookID(str(uuid.uuid4()))
        book = Book(id=new_id, title=title)
        BUGGY_BOOKS[new_id] = book
        return book

    @strawberry.mutation
    def updateBook(self, id: BookID, title: str) -> Book | None:
        if id in BUGGY_BOOKS and title:
            raise RuntimeError("planted: update-on-existing")
        return BUGGY_BOOKS.get(id)


schema = strawberry.Schema(Query, Mutation)
