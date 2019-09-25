from textwrap import dedent

import pytest

import schemathesis

from .utils import make_schema

pytest_plugins = ["pytester", "aiohttp.pytest_plugin"]


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
def swagger_20(simple_schema):
    return schemathesis.from_dict(simple_schema)


@pytest.fixture()
def case_factory():

    defaults = {"method": "GET", "headers": {}, "query": [], "body": {}, "cookies": {}, "form_data": {}}

    def maker(**kwargs):
        return schemathesis.Case(**{**defaults, **kwargs})

    return maker


@pytest.fixture()
def testdir(testdir):
    def maker(content, **kwargs):
        schema = make_schema(**kwargs)
        preparation = dedent(
            """
        import pytest
        import schemathesis
        from test.utils import *
        from hypothesis import settings
        raw_schema = {schema}
        schema = schemathesis.from_dict(raw_schema)
        """.format(
                schema=schema
            )
        )
        testdir.makepyfile(preparation, content)
        testdir.makepyfile(
            conftest=dedent(
                """
        def pytest_configure(config):
            config.HYPOTHESIS_CASES = 0
        def pytest_unconfigure(config):
            print(f"Hypothesis calls: {config.HYPOTHESIS_CASES}")
        """
            )
        )

    testdir.make_test = maker

    def run_and_assert(*args, **kwargs):
        result = testdir.runpytest(*args)
        result.assert_outcomes(**kwargs)

    testdir.run_and_assert = run_and_assert

    return testdir
