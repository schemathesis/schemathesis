import pytest


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("behavior", "args"),
    [
        ("ok", ()),
        ("ok", ("-H", "Authorization: Bearer token-0")),
        ("register-rejects", ()),
        ("login-rejects", ()),
        ("no-token", ()),
        ("unsatisfiable-password", ()),
    ],
    ids=["signed-up", "skipped", "register-rejects", "login-rejects", "no-token", "unsatisfiable-password"],
)
def test_auth_bootstrap_output(ctx, cli, snapshot_cli, behavior, args):
    api = ctx.openapi.apps.sign_up_and_login(behavior)
    assert cli.run(api.schema_url, "--phases=examples", *args) == snapshot_cli


@pytest.mark.parametrize("behavior", ["ok", "undeclared-security"])
def test_signed_up_token_reaches_protected_operations(ctx, cli, behavior):
    api = ctx.openapi.apps.sign_up_and_login(behavior)
    cli.run(api.schema_url, "--phases=fuzzing", "--max-examples=5")
    assert any(
        request.headers.get("Authorization", "").startswith("Bearer token-")
        for request in api.requests
        if request.path == "/orders"
    )
