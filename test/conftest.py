import pytest

from schemathesis import Case

pytest_plugins = ["pytester"]


@pytest.fixture
def simple_schema():
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/users": {
                "get": {
                    "summary": "Returns a list of users.",
                    "description": "Optional extended description in Markdown.",
                    "produces": ["application/json"],
                    "responses": {200: {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture()
def case_factory():

    defaults = {"method": "GET", "headers": {}, "query": [], "body": {}}

    def maker(**kwargs):
        return Case(**{**defaults, **kwargs})

    return maker
