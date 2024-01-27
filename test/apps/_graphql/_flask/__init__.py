import logging

from flask import Flask
from strawberry.flask.views import GraphQLView

from ..schema import schema as default_schema

logging.getLogger("werkzeug").setLevel(logging.CRITICAL)


def create_app(path="/graphql", schema=default_schema, **kwargs):
    app = Flask("test_app")
    app.add_url_rule(path, view_func=GraphQLView.as_view("graphql", schema=schema, **kwargs))
    return app
