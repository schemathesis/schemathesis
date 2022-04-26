from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from ..schema import schema


def create_app(path="/graphql"):
    app = FastAPI()
    graphql_app = GraphQLRouter(schema)
    app.include_router(graphql_app, prefix=path)
    return app
