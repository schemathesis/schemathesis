import enum

import strawberry
from flask import Flask
from strawberry.flask.views import GraphQLView

SECRET_CODE = "gx7q2m4v9k1p3z"
SECRET_NUMBER = 8675309
SECRET_RATIO = 3.5


@strawberry.enum
class Color(enum.Enum):
    # Values 0/1 are filtered out of the constant pool, keeping it free of enum noise.
    RED = 0
    GREEN = 1


@strawberry.input
class Filter:
    code: str
    size: int


@strawberry.type
class Container:
    @strawberry.field
    def item(self, code: str) -> bool:
        return code == SECRET_CODE


@strawberry.type
class Query:
    @strawberry.field
    def container(self) -> Container:
        return Container()

    @strawberry.field
    def lookup(self, code: str) -> bool:
        return code == SECRET_CODE

    @strawberry.field
    def by_id(self, id: strawberry.ID) -> bool:
        return str(id) == SECRET_CODE

    @strawberry.field
    def by_number(self, n: int) -> bool:
        return n == SECRET_NUMBER

    @strawberry.field
    def by_ratio(self, r: float) -> bool:
        return r == SECRET_RATIO

    @strawberry.field
    def by_tags(self, tags: list[str]) -> bool:
        return SECRET_CODE in tags

    @strawberry.field
    def by_filter(self, filter: Filter) -> bool:
        return filter.code == SECRET_CODE and filter.size == SECRET_NUMBER

    @strawberry.field
    def by_optional_filter(self, filter: Filter | None) -> bool:
        return filter is not None and filter.code == SECRET_CODE

    @strawberry.field
    def by_flag(self, flag: bool) -> bool:
        return flag

    @strawberry.field
    def by_color(self, color: Color) -> bool:
        return color == Color.RED


schema = strawberry.Schema(Query)

app = Flask(__name__)
app.add_url_rule("/graphql", view_func=GraphQLView.as_view("view", schema=schema))
