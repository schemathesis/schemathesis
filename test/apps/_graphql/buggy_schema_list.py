"""GraphQL schema with a bug gated on real ids passed via list-typed arguments."""

from __future__ import annotations

import uuid

import strawberry


@strawberry.type
class Book:
    id: strawberry.ID
    title: str


BUGGY_BOOKS: dict[str, Book] = {}


@strawberry.type
class Query:
    @strawberry.field
    def booksByIds(self, bookIds: list[strawberry.ID]) -> list[Book]:
        if any(bid in BUGGY_BOOKS for bid in bookIds):
            raise RuntimeError("planted: batch-lookup-on-existing")
        return []


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addBook(self, title: str) -> Book:
        new_id = strawberry.ID(str(uuid.uuid4()))
        book = Book(id=new_id, title=title)
        BUGGY_BOOKS[new_id] = book
        return book


schema = strawberry.Schema(Query, Mutation)
