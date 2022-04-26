from flask import Flask
from strawberry.flask.views import GraphQLView

from ..schema import schema


def create_app(path="/graphql", **kwargs):
    app = Flask("test_app")
    app.add_url_rule(path, view_func=GraphQLView.as_view("graphql", schema=schema, **kwargs))
    return app
