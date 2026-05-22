import pytest

pytest.importorskip("flask")

from flask import Flask

from schemathesis.python._constants.adapters.flask import FlaskAdapter


def _make_app():
    app = Flask(__name__)

    @app.route("/users")
    def users():
        return ""

    @app.route("/items/<int:item_id>")
    def items(item_id):
        return ""

    return app


def test_matches_real_flask_app():
    assert FlaskAdapter().matches(_make_app()) is True


def test_does_not_match_non_flask():
    assert FlaskAdapter().matches(object()) is False


def test_handlers_returns_view_functions():
    handlers = list(FlaskAdapter().handlers(_make_app()))
    names = {h.__name__ for h in handlers}
    assert "users" in names
    assert "items" in names
