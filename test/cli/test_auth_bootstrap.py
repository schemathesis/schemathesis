import json
from xml.etree import ElementTree

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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_bootstrap_output_on_narrow_terminal(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.sign_up_and_login("ok")
    # Dropping PYTEST_VERSION lifts the fixed console width; drop FORCE_COLOR too, or CI renders live progress
    env = {"PYTEST_VERSION": None, "FORCE_COLOR": None, "COLUMNS": "80"}
    assert cli.run(api.schema_url, "--phases=examples", env=env) == snapshot_cli


@pytest.mark.parametrize("behavior", ["ok", "undeclared-security"])
def test_signed_up_token_reaches_protected_operations(ctx, cli, behavior):
    api = ctx.openapi.apps.sign_up_and_login(behavior)
    cli.run(api.schema_url, "--phases=fuzzing", "--max-examples=5")
    assert any(
        request.headers.get("Authorization", "").startswith("Bearer token-")
        for request in api.requests
        if request.path == "/orders"
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_auth_auto_signup_disabled(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.sign_up_and_login()
    assert cli.run(api.schema_url, "--phases=examples", config={"auth": {"auto-signup": False}}) == snapshot_cli
    assert [request.path for request in api.requests if request.path.startswith("/auth/")] == []


def test_reports_leave_out_sign_up_and_login_calls(ctx, cli, tmp_path):
    api = ctx.openapi.apps.sign_up_and_login()
    cli.run(
        api.schema_url,
        "--phases=stateful",
        f"--report-wfc-path={tmp_path / 'wfc.json'}",
        f"--report-junit-path={tmp_path / 'junit.xml'}",
        f"--report-allure-path={tmp_path / 'allure'}",
    )
    wfc = json.loads((tmp_path / "wfc.json").read_text())
    junit = ElementTree.parse(tmp_path / "junit.xml")
    assert {
        "wfc": (wfc["totalTests"], wfc["problemDetails"]["rest"]["evaluatedHttpCalls"]),
        "junit": [case.get("name") for case in junit.iter("testcase")],
        "allure": sorted(path.name for path in (tmp_path / "allure").glob("*-result.json")),
    } == {"wfc": (0, 0), "junit": [], "allure": []}
