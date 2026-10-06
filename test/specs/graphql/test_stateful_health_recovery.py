from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("book_id_scalar")


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
    assert "Unhealthy API" not in result.stdout
    assert "UnhealthyAPIError" not in result.stdout
    assert "Stateful" in result.stdout
