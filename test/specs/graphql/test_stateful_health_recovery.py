from __future__ import annotations

import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS


@pytest.fixture(autouse=True)
def _register_book_id_scalar():
    schemathesis.graphql.scalar("BookID", st.uuids().map(str).map(nodes.String))
    yield
    CUSTOM_SCALARS.clear()


# Need enough scenarios to exercise the per-operation demote path while other resolvers stay healthy.
def test_one_slow_resolver_does_not_abort_stateful_phase(ctx, cli):
    api = ctx.graphql.apps.slow_mutation()
    result = cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=20",
        "--request-timeout=0.5",
        "-m",
        "positive",
        "-c",
        "not_a_server_error",
    )
    assert "API appears unhealthy" not in result.stdout
    assert "UnhealthyAPIError" not in result.stdout
    assert "Stateful" in result.stdout
