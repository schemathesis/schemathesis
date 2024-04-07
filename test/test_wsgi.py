import pytest
from flask import jsonify, request
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.models import Case


@pytest.fixture()
def schema(flask_app):
    return schemathesis.from_wsgi("/schema.yaml", flask_app)


@pytest.mark.parametrize("method", ("call", "call_wsgi"))
@pytest.mark.hypothesis_nested
def test_cookies(flask_app, method):
    @flask_app.route("/cookies", methods=["GET"])
    def cookies():
        return jsonify(request.cookies)

    schema = schemathesis.from_dict(
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
        app=flask_app,
    )

    strategy = schema["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = getattr(case, method)()
        assert response.status_code == 200
        assert response.json == {"token": "test"}

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("multipart")
def test_form_data(schema):
    strategy = schema["/multipart"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        # converted to string in the app
        assert response.json == {key: str(value) for key, value in case.body.items()}

    test()


def test_not_wsgi(schema):
    # When a schema is created without a WSGI app (e.g. from a URL)
    case = Case(schema["/success"]["GET"], generation_time=0.0)
    case.operation.app = None
    # Then an error should be raised if the user tries to use `call_wsgi`
    with pytest.raises(
        RuntimeError,
        match="WSGI application instance is required. "
        "Please, set `app` argument in the schema constructor or pass it to `call_wsgi`",
    ):
        case.call_wsgi()


@pytest.mark.hypothesis_nested
def test_binary_body(mocker, flask_app):
    # When an API operation accepts a binary input
    schema = schemathesis.from_dict(
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
        app=flask_app,
    )
    strategy = schema["/api/upload_file"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json == {"size": mocker.ANY}

    # Then it should be sent correctly
    test()


def test_app_with_parametrize(testdir):
    # Regression - missed argument inside "wrapper" in `BaseSchema.parametrize`
    testdir.makepyfile(
        """
    import schemathesis
    from test.apps.openapi._flask.app import app
    from hypothesis import settings

    schema = schemathesis.from_wsgi("/schema.yaml", app)

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
