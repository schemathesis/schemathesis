import strawberry


@strawberry.type
class Book:
    title: str
    author: "Author"


@strawberry.type
class Author:
    name: str
    books: list[Book]


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
    def getBooks(self) -> list[Book]:
        return list(BOOKS.values())

    @strawberry.field
    def getAuthors(self) -> list[Author]:
        return list(AUTHORS.values())


def get_or_create_author(name: str) -> tuple[int, Author]:
    for author_id, author in AUTHORS.items():  # noqa: B007
        if author.name == name:
            break
    else:
        author = Author(name=name, books=[])
        author_id = len(AUTHORS) + 1
        AUTHORS[author_id] = author
    return author_id, author


@strawberry.type
class Mutation:
    @strawberry.mutation
    def addBook(self, title: str, author: str) -> Book:
        for book in BOOKS.values():
            if book.title == title:
                break
        else:
            # New book and potentially new author
            author_id, author = get_or_create_author(author)
            book = Book(title=title, author=author)
            book_id = len(BOOKS) + 1
            BOOKS[book_id] = book
            author.books.append(book)
        return book

    @strawberry.mutation
    def addAuthor(self, name: str) -> Author:
        return get_or_create_author(name)[1]


schema = strawberry.Schema(Query, Mutation)
