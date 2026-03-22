from __future__ import annotations

import pytest
from flask import jsonify


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_basic(cli, ctx, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    result = cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2")
    assert result == snapshot_cli
