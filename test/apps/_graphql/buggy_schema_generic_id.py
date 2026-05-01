"""GraphQL schema with bugs gated on real ids exposed via generic `ID!` — reachable only via argument-name / enclosing-type matching."""

from __future__ import annotations

import uuid

import strawberry


@strawberry.type
class User:
    id: strawberry.ID
    name: str


@strawberry.type
class Author:
    id: strawberry.ID
    name: str


@strawberry.type
class Post:
    id: strawberry.ID
    title: str


BUGGY_USERS: dict[str, User] = {}
BUGGY_AUTHORS: dict[str, Author] = {}


@strawberry.type
class Query:
    @strawberry.field
    def user(self, id: strawberry.ID) -> User | None:
        if id in BUGGY_USERS:
            raise RuntimeError("planted: use-user-after-create")
        return None

    @strawberry.field
    def postsByAuthor(self, authorId: strawberry.ID) -> list[Post]:
        if authorId in BUGGY_AUTHORS:
            raise RuntimeError("planted: posts-by-author")
        return []


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addUser(self, name: str) -> User:
        new_id = strawberry.ID(str(uuid.uuid4()))
        user = User(id=new_id, name=name)
        BUGGY_USERS[new_id] = user
        return user

    @strawberry.mutation
    def addAuthor(self, name: str) -> Author:
        new_id = strawberry.ID(str(uuid.uuid4()))
        author = Author(id=new_id, name=name)
        BUGGY_AUTHORS[new_id] = author
        return author


schema = strawberry.Schema(Query, Mutation)
