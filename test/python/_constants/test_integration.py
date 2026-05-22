"""End-to-end constants extraction against a real Python app."""

import pytest

pytest.importorskip("fastapi")

import schemathesis
from schemathesis.python._constants.adapters import default_adapters
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.registry import default_registry


def test_extracts_constants_from_fastapi_app_via_public_decorator():
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/users")
    def list_users():
        STATUS_ACTIVE = "active"  # noqa: F841
        STATUS_INACTIVE = "inactive"  # noqa: F841
        return []

    default_registry().clear()
    try:

        @schemathesis.python.constants
        def from_app():
            return app

        result = extract_all(registry=default_registry(), adapters=default_adapters())
    finally:
        default_registry().clear()

    strings = set(result.pool.values_for("string"))
    assert "active" in strings
    assert "inactive" in strings
    assert "fastapi" in result.per_adapter
    assert result.per_adapter["fastapi"] > 0
