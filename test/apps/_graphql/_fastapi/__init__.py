from fastapi import FastAPI
from starlette.graphql import GraphQLApp

from ..schema import schema


def create_app(path="/graphql"):
    app = FastAPI()
    app.add_route(path, GraphQLApp(schema=schema))
    return app
