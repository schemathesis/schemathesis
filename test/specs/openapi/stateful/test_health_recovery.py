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
    # Phase must NOT abort.
    assert "Unhealthy API" not in result.stdout
    assert "UnhealthyAPIError" not in result.stdout
    # Phase actually ran (negative-only assertions could pass on a no-op skip).
    assert "Stateful" in result.stdout


# Three linked operations hang while creating and listing users keep answering, so the API is alive.
def test_operations_that_keep_hanging_are_dropped_while_others_respond(ctx, cli):
    hanging = ["/users/<int:user_id>", "/users/<user_id>"]
    api = ctx.openapi.apps.stateful_users(
        SlowOperations({(method, rule): 0.3 for method in ("GET", "PATCH", "DELETE") for rule in hanging}),
    )
    result = cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=20",
        "--request-timeout=0.1",
        "-c",
        "not_a_server_error",
    )
    assert "Unhealthy API" not in result.stdout
    assert "Unresponsive Operation" in result.stdout
    hanging_requests = [request for request in api.requests if request.path.startswith("/users/")]
    assert len(hanging_requests) < 100, len(hanging_requests)


# Nothing answers, so the phase aborts once the API stays silent for a whole window.
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
    # Phase aborts with the rich health-monitor message listing the failing entry operations.
    assert "API stopped responding" in result.stdout
    assert "/users (" in result.stdout
    assert result.exit_code != 0
