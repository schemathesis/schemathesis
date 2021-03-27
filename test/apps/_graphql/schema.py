import graphene


class Book(graphene.ObjectType):
    title = graphene.String()
    author = graphene.Field(lambda: Author)

    def resolve_author(self, info):
        return AUTHORS.get(self.author)


class Author(graphene.ObjectType):
    name = graphene.String()
    books = graphene.List(Book)

    def resolve_books(self, info):
        if len(info.path.as_list()) > 7:
            raise ValueError("Hidden bug")
        return [BOOKS.get(book) for book in self.books]


AUTHORS = {
    1: Author(name="J.R.R Tolkien", books=[1, 2, 3]),
    2: Author(name="Tove Marika Jansson", books=[4, 5, 6]),
}

BOOKS = {
    1: Book(title="The Fellowship of the Ring", author=1),
    2: Book(title="The Two Towers", author=1),
    3: Book(title="The Return of the King", author=1),
    4: Book(title="Kometen kommer", author=2),
    5: Book(title="Trollvinter", author=2),
    6: Book(title="Farlig midsommar", author=2),
}


class Query(graphene.ObjectType):
    getBooks = graphene.List(Book)
    getAuthors = graphene.List(Author)

    def resolve_getBooks(root, info):
        return list(BOOKS.values())

    def resolve_getAuthors(root, info):
        return list(AUTHORS.values())


schema = graphene.Schema(query=Query)
