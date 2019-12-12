import pytest
from flask import jsonify, request
from hypothesis import given

import schemathesis
from schemathesis import Case


@pytest.fixture()
def schema(flask_app):
    return schemathesis.from_wsgi("/swagger.yaml", flask_app)


def test_call(schema, simple_schema):
    strategy = schema.endpoints["/api/success"]["GET"].as_strategy()

    @given(case=strategy)
    def test(case):
        response = case.call_wsgi()
        assert response.status_code == 200
        assert response.json == {"success": True}

    test()


def test_cookies(flask_app):
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
                                "schema": {"type": "string", "const": "test"},
                            }
                        ]
                    }
                }
            },
        },
        app=flask_app,
    )

    strategy = schema.endpoints["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    def test(case):
        response = case.call_wsgi()
        assert response.status_code == 200
        assert response.json == {"token": "test"}

    test()


@pytest.mark.endpoints("multipart")
def test_form_data(schema):
    strategy = schema.endpoints["/api/multipart"]["POST"].as_strategy()

    @given(case=strategy)
    def test(case):
        response = case.call_wsgi()
        assert response.status_code == 200
        # converted to string in the app
        assert response.json == {key: str(value) for key, value in case.form_data.items()}

    test()


def test_not_wsgi(schema):
    case = Case(schema.endpoints["/api/success"]["GET"])
    case.endpoint.app = None
    with pytest.raises(
        RuntimeError,
        match="WSGI application instance is required. "
        "Please, set `app` argument in the schema constructor or pass it to `call_wsgi`",
    ):
        case.call_wsgi()
