from __future__ import annotations

from test.apps.catalog.openapi.modifiers.stateful import Slowdown, SlowOperations


# Need enough scenarios to exercise the per-operation demote path while other operations stay healthy.
def test_one_slow_operation_does_not_abort_stateful_phase(ctx, cli):
    api = ctx.openapi.apps.stateful_users(
        SlowOperations({("DELETE", "/users/<int:user_id>"): 2.0}),
    )
    result = cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=20",
        "--request-timeout=0.5",
        "-c",
        "not_a_server_error",
    )
    # Phase must NOT abort: no "API appears unhealthy" message, no UnhealthyAPIError.
    assert "API appears unhealthy" not in result.stdout
    assert "UnhealthyAPIError" not in result.stdout
    # Phase actually ran (negative-only assertions could pass on a no-op skip).
    assert "Stateful" in result.stdout


# Need enough scenarios to fan out across distinct operations and cross the phase-fatal abort threshold.
def test_phase_aborts_when_all_ops_fail_transport(ctx, cli):
    api = ctx.openapi.apps.stateful_users(Slowdown(seconds=2.0))
    result = cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=20",
        "--request-timeout=0.5",
        "-c",
        "not_a_server_error",
    )
    # Phase aborts with the rich health-monitor message and at least 3 offending operations listed.
    assert "API appears unhealthy" in result.stdout
    assert result.stdout.count("(last failure") >= 3
    assert result.exit_code != 0
