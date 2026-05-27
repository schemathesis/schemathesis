from __future__ import annotations

import pytest
from flask import Flask
from hypothesis import HealthCheck, given, settings


@pytest.mark.hypothesis_nested
def test_wsgi_reraises_server_exception(ctx):
    app = Flask(__name__)

    @app.route("/api/crash", methods=["GET"])
    def crash():
        raise ValueError("something broke")

    schema = ctx.openapi.load_schema({"/api/crash": {"get": {"responses": {"200": {"description": "OK"}}}}})
    strategy = schema["/api/crash"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        with pytest.raises(ValueError, match="something broke"):
            case.call(app=app)

    test()
