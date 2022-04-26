from typing import List

import strawberry


@strawberry.type
class Book:
    title: str
    author: "Author"


@strawberry.type
class Author:
    name: str
    books: List[Book]


TOLKIEN = Author(name="J.R.R Tolkien", books=[])
JANSSON = Author(name="Tove Marika Jansson", books=[])


BOOKS = {
    1: Book(title="The Fellowship of the Ring", author=TOLKIEN),
    2: Book(title="The Two Towers", author=TOLKIEN),
    3: Book(title="The Return of the King", author=TOLKIEN),
    4: Book(title="Kometen kommer", author=JANSSON),
    5: Book(title="Trollvinter", author=JANSSON),
    6: Book(title="Farlig midsommar", author=JANSSON),
}
TOLKIEN.books = [BOOKS[1], BOOKS[2], BOOKS[3]]
JANSSON.books = [BOOKS[4], BOOKS[5], BOOKS[6]]

AUTHORS = {
    1: TOLKIEN,
    2: JANSSON,
}


@strawberry.type
class Query:
    @strawberry.field
    def getBooks(self) -> List[Book]:
        return list(BOOKS.values())

    @strawberry.field
    def getAuthors(self) -> List[Author]:
        return list(AUTHORS.values())


schema = strawberry.Schema(Query)
