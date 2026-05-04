import pytest
from flask import Flask, jsonify, request
from hypothesis import HealthCheck, given, settings

import schemathesis


@pytest.mark.hypothesis_nested
def test_cookies():
    app = Flask(__name__)

    @app.route("/cookies", methods=["GET"])
    def cookies():
        return jsonify(request.cookies)

    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/cookies": {
                    "get": {
                        "parameters": [
                            {
                                "name": "token",
                                "in": "cookie",
                                "required": True,
                                "schema": {"type": "string", "enum": ["test"]},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )

    strategy = schema["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call(app=app)
        assert response.status_code == 200
        assert response.json() == {"token": "test"}

    test()


@pytest.mark.hypothesis_nested
def test_form_data(ctx):
    api = ctx.openapi.apps.multipart()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    strategy = schema["/api/multipart"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        # converted to string in the app
        assert response.json() == {key: str(value) for key, value in case.body.items()}

    test()


@pytest.mark.hypothesis_nested
def test_binary_body(ctx, mocker):
    # When an API operation accepts a binary input
    api = ctx.openapi.apps.upload_file()
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/api/upload_file": {
                    "post": {
                        "requestBody": {
                            "content": {"application/octet-stream": {"schema": {"format": "binary", "type": "string"}}}
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )
    strategy = schema["/api/upload_file"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call(app=api.wsgi_app)
        assert response.status_code == 200
        assert response.json() == {"size": mocker.ANY}

    # Then it should be sent correctly
    test()


def test_app_with_parametrize(testdir):
    # Regression - missed argument inside "wrapper" in `BaseSchema.parametrize`
    testdir.makepyfile(
        """
    import schemathesis
    from hypothesis import settings
    from test.apps.catalog.openapi.basic import success_and_failure

    api = success_and_failure()
    app = api.server
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

    called = False

    @schema.parametrize()
    @settings(max_examples=1)
    def test(case):
        global called
        called = True
        assert case.operation.schema.app is app

    def test_two():
        assert called
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=3)
