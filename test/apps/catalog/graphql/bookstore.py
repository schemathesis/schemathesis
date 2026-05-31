import logging
import time
import uuid
from typing import Literal, NewType

import strawberry
from fastapi import FastAPI
from flask import Flask
from strawberry.fastapi import GraphQLRouter
from strawberry.flask.views import GraphQLView
from strawberry.schema.config import StrawberryConfig
from strawberry.types.scalar import ScalarDefinition

try:
    from test.apps.runtime import GraphQLApp
except ImportError:
    # Script-mode launcher (test/apps/__init__.py invoked directly) puts test/apps on sys.path.
    from runtime import GraphQLApp

logging.getLogger("werkzeug").setLevel(logging.CRITICAL)


def _create_flask(endpoint: str, schema: strawberry.Schema) -> Flask:
    app = Flask("test_app")
    app.add_url_rule(endpoint, view_func=GraphQLView.as_view("graphql", schema=schema))
    return app


def _create_fastapi(endpoint: str, schema: strawberry.Schema) -> FastAPI:
    app = FastAPI()
    app.include_router(GraphQLRouter(schema), prefix=endpoint)
    return app


def _wrap(schema: strawberry.Schema, endpoint: str, framework: Literal["flask", "fastapi"]) -> GraphQLApp:
    if framework == "flask":
        return GraphQLApp(server=_create_flask(endpoint, schema), kind="flask", endpoint=endpoint)
    return GraphQLApp(server=_create_fastapi(endpoint, schema), kind="fastapi", endpoint=endpoint)


BookID = NewType("BookID", str)


@strawberry.type
class Book:
    id: BookID
    title: str


def _book_id_config() -> StrawberryConfig:
    return StrawberryConfig(
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
    )


@strawberry.type
class DefaultBook:
    title: str
    author: "DefaultAuthor"


@strawberry.type
class DefaultAuthor:
    name: str
    books: list[DefaultBook]


def _make_default() -> strawberry.Schema:
    tolkien = DefaultAuthor(name="J.R.R Tolkien", books=[])
    jansson = DefaultAuthor(name="Tove Marika Jansson", books=[])

    books = {
        1: DefaultBook(title="The Fellowship of the Ring", author=tolkien),
        2: DefaultBook(title="The Two Towers", author=tolkien),
        3: DefaultBook(title="The Return of the King", author=tolkien),
        4: DefaultBook(title="Kometen kommer", author=jansson),
        5: DefaultBook(title="Trollvinter", author=jansson),
        6: DefaultBook(title="Farlig midsommar", author=jansson),
    }
    tolkien.books = [books[1], books[2], books[3]]
    jansson.books = [books[4], books[5], books[6]]

    authors = {1: tolkien, 2: jansson}

    def get_or_create_author(name: str) -> tuple[int, DefaultAuthor]:
        for author_id, author in authors.items():  # noqa: B007
            if author.name == name:
                break
        else:
            author = DefaultAuthor(name=name, books=[])
            author_id = len(authors) + 1
            authors[author_id] = author
        return author_id, author

    @strawberry.type
    class Query:
        @strawberry.field
        def getBooks(self) -> list[DefaultBook]:
            return list(books.values())

        @strawberry.field
        def getAuthors(self) -> list[DefaultAuthor]:
            return list(authors.values())

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str, author: str) -> DefaultBook:
            for book in books.values():
                if book.title == title:
                    break
            else:
                _, found_author = get_or_create_author(author)
                book = DefaultBook(title=title, author=found_author)
                book_id = len(books) + 1
                books[book_id] = book
                found_author.books.append(book)
            return book

        @strawberry.mutation
        def addAuthor(self, name: str) -> DefaultAuthor:
            return get_or_create_author(name)[1]

    return strawberry.Schema(Query, Mutation)


def _make_use_after_create() -> strawberry.Schema:
    books: dict[str, Book] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: BookID) -> Book | None:
            if id in books:
                raise RuntimeError("planted: use-after-create")
            return None

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            new_id = BookID(str(uuid.uuid4()))
            book = Book(id=new_id, title=title)
            books[new_id] = book
            return book

        @strawberry.mutation
        def updateBook(self, id: BookID, title: str) -> Book | None:
            if id in books and title:
                raise RuntimeError("planted: update-on-existing")
            return books.get(id)

    return strawberry.Schema(Query, Mutation, config=_book_id_config())


def _make_tombstone() -> strawberry.Schema:
    books: dict[str, Book] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: BookID) -> Book | None:
            return books.get(id)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            new_id = BookID(str(uuid.uuid4()))
            book = Book(id=new_id, title=title)
            books[new_id] = book
            return book

        @strawberry.mutation
        def deleteBook(self, id: BookID) -> bool:
            books.pop(id, None)
            return True

        @strawberry.mutation
        def updateBook(self, id: BookID, title: str) -> Book | None:
            if id in books and title:
                raise RuntimeError("planted: update-on-existing")
            return None

    return strawberry.Schema(Query, Mutation, config=_book_id_config())


def _make_use_after_delete() -> strawberry.Schema:
    books: dict[str, Book] = {}
    deleted: set[str] = set()

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: BookID) -> Book | None:
            if id in deleted:
                raise RuntimeError("planted: use-after-delete")
            return books.get(id)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            new_id = BookID(str(uuid.uuid4()))
            book = Book(id=new_id, title=title)
            books[new_id] = book
            return book

        @strawberry.mutation
        def deleteBook(self, id: BookID) -> bool:
            if id in books:
                del books[id]
                deleted.add(id)
            return True

    return strawberry.Schema(Query, Mutation, config=_book_id_config())


def _make_slow_mutation() -> strawberry.Schema:
    books: dict[str, Book] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: BookID) -> Book | None:
            return books.get(id)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            new_id = BookID(str(uuid.uuid4()))
            book = Book(id=new_id, title=title)
            books[new_id] = book
            return book

        @strawberry.mutation
        def deleteBook(self, id: BookID) -> bool:
            time.sleep(2.0)
            books.pop(id, None)
            return True

    return strawberry.Schema(Query, Mutation, config=_book_id_config())


def _make_double_delete() -> strawberry.Schema:
    books: dict[str, Book] = {}
    deleted: set[str] = set()

    @strawberry.type
    class Query:
        @strawberry.field
        def book(self, id: BookID) -> Book | None:
            return books.get(id)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> Book:
            new_id = BookID(str(uuid.uuid4()))
            book = Book(id=new_id, title=title)
            books[new_id] = book
            return book

        @strawberry.mutation
        def deleteBook(self, id: BookID) -> bool:
            if id in deleted:
                raise RuntimeError("planted: double-delete")
            if id in books:
                del books[id]
                deleted.add(id)
            return True

    return strawberry.Schema(Query, Mutation, config=_book_id_config())


def _make_generic_id() -> strawberry.Schema:
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

    users: dict[str, User] = {}
    authors: dict[str, Author] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def user(self, id: strawberry.ID) -> User | None:
            if id in users:
                raise RuntimeError("planted: use-user-after-create")
            return None

        @strawberry.field
        def postsByAuthor(self, authorId: strawberry.ID) -> list[Post]:
            if authorId in authors:
                raise RuntimeError("planted: posts-by-author")
            return []

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addUser(self, name: str) -> User:
            new_id = strawberry.ID(str(uuid.uuid4()))
            user = User(id=new_id, name=name)
            users[new_id] = user
            return user

        @strawberry.mutation
        def addAuthor(self, name: str) -> Author:
            new_id = strawberry.ID(str(uuid.uuid4()))
            author = Author(id=new_id, name=name)
            authors[new_id] = author
            return author

    return strawberry.Schema(Query, Mutation)


def _make_input_object() -> strawberry.Schema:
    @strawberry.type
    class Author:
        id: strawberry.ID
        name: str

    @strawberry.input
    class UpdateAuthorInput:
        authorId: strawberry.ID
        name: str

    authors: dict[str, Author] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def author(self, id: strawberry.ID) -> Author | None:
            return authors.get(id)

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addAuthor(self, name: str) -> Author:
            new_id = strawberry.ID(str(uuid.uuid4()))
            author = Author(id=new_id, name=name)
            authors[new_id] = author
            return author

        @strawberry.mutation
        def updateAuthor(self, input: UpdateAuthorInput) -> Author | None:
            if input.authorId in authors and input.name:
                raise RuntimeError("planted: input-object-update")
            return authors.get(input.authorId)

    return strawberry.Schema(Query, Mutation)


def _make_list_argument() -> strawberry.Schema:
    @strawberry.type(name="Book")
    class ListBook:
        id: strawberry.ID
        title: str

    books: dict[str, ListBook] = {}

    @strawberry.type
    class Query:
        @strawberry.field
        def booksByIds(self, bookIds: list[strawberry.ID]) -> list[ListBook]:
            if any(bid in books for bid in bookIds):
                raise RuntimeError("planted: batch-lookup-on-existing")
            return []

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def addBook(self, title: str) -> ListBook:
            new_id = strawberry.ID(str(uuid.uuid4()))
            book = ListBook(id=new_id, title=title)
            books[new_id] = book
            return book

    return strawberry.Schema(Query, Mutation)


def _make_non_id_pool() -> strawberry.Schema:
    @strawberry.type
    class Project:
        id: strawberry.ID
        fullPath: str

    projects = {
        "acme/web": Project(id=strawberry.ID("1"), fullPath="acme/web"),
        "acme/api": Project(id=strawberry.ID("2"), fullPath="acme/api"),
    }

    @strawberry.type
    class Query:
        @strawberry.field
        def projects(self) -> list[Project]:
            return list(projects.values())

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def moveIssue(self, projectPath: str, title: str) -> bool:
            if projectPath in projects:
                raise RuntimeError("planted: move-issue-to-real-project")
            return False

    return strawberry.Schema(Query, Mutation)


def books(*, endpoint: str = "/graphql", framework: Literal["flask", "fastapi"] = "flask") -> GraphQLApp:
    return _wrap(_make_default(), endpoint, framework)


def from_schema(
    schema: strawberry.Schema,
    *,
    endpoint: str = "/graphql",
    framework: Literal["flask", "fastapi"] = "flask",
) -> GraphQLApp:
    return _wrap(schema, endpoint, framework)


def use_after_create(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_use_after_create(), endpoint, "flask")


def tombstone_pool(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_tombstone(), endpoint, "flask")


def use_after_delete(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_use_after_delete(), endpoint, "flask")


def slow_mutation(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_slow_mutation(), endpoint, "flask")


def double_delete(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_double_delete(), endpoint, "flask")


def generic_id_pool(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_generic_id(), endpoint, "flask")


def input_object_pool(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_input_object(), endpoint, "flask")


def list_argument_pool(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_list_argument(), endpoint, "flask")


def non_id_pool(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_non_id_pool(), endpoint, "flask")


def _make_bare_slug() -> strawberry.Schema:
    @strawberry.type
    class Project:
        id: strawberry.ID
        slug: str

    seeded = {"acme-web": Project(id=strawberry.ID("1"), slug="acme-web")}

    @strawberry.type
    class Query:
        @strawberry.field
        def projects(self) -> list[Project]:
            return list(seeded.values())

        @strawberry.field
        def project(self, slug: str) -> Project | None:
            if slug in seeded:
                raise RuntimeError("planted: project-by-real-slug")
            return None

    return strawberry.Schema(Query)


def bare_slug(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_bare_slug(), endpoint, "flask")


def _make_relay_connection() -> strawberry.Schema:
    @strawberry.type
    class Product:
        id: strawberry.ID
        slug: str

    @strawberry.type
    class ProductEdge:
        node: Product

    @strawberry.type
    class ProductConnection:
        edges: list[ProductEdge]

    seeded = {f"chair-{n:02d}": Product(id=strawberry.ID(str(n)), slug=f"chair-{n:02d}") for n in range(1, 6)}

    @strawberry.type
    class Query:
        @strawberry.field
        def products(self) -> ProductConnection:
            return ProductConnection(edges=[ProductEdge(node=p) for p in seeded.values()])

    @strawberry.type
    class Mutation:
        @strawberry.mutation
        def archiveProduct(self, productSlug: str) -> bool:
            if productSlug in seeded:
                raise RuntimeError("planted: archive-product-by-real-slug")
            return False

    return strawberry.Schema(Query, Mutation)


def relay_connection(*, endpoint: str = "/graphql") -> GraphQLApp:
    return _wrap(_make_relay_connection(), endpoint, "flask")
