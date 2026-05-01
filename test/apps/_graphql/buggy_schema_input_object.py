"""GraphQL schema with bugs gated on real ids passed via input-object arguments."""

from __future__ import annotations

import uuid

import strawberry


@strawberry.type
class Author:
    id: strawberry.ID
    name: str


@strawberry.input
class UpdateAuthorInput:
    authorId: strawberry.ID
    name: str


BUGGY_AUTHORS: dict[str, Author] = {}


@strawberry.type
class Query:
    @strawberry.field
    def author(self, id: strawberry.ID) -> Author | None:
        return BUGGY_AUTHORS.get(id)


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addAuthor(self, name: str) -> Author:
        new_id = strawberry.ID(str(uuid.uuid4()))
        author = Author(id=new_id, name=name)
        BUGGY_AUTHORS[new_id] = author
        return author

    @strawberry.mutation
    def updateAuthor(self, input: UpdateAuthorInput) -> Author | None:
        if input.authorId in BUGGY_AUTHORS and input.name:
            raise RuntimeError("planted: input-object-update")
        return BUGGY_AUTHORS.get(input.authorId)


schema = strawberry.Schema(Query, Mutation)
