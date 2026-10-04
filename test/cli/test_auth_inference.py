from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("reset_feedback_pipeline")

VALID_TOKEN = "real-token"


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    "extra_kwargs",
    [
        {},
        {"config": {"auth": {"openapi": {"BearerAuth": {"bearer": VALID_TOKEN}}}}},
    ],
    ids=["no-creds", "with-creds"],
)
def test_auth_inference_toggles_protected_access(ctx, cli, snapshot_cli, extra_kwargs):
    api = ctx.openapi.apps.under_declared_security()
    assert cli.run(api.schema_url, "--max-examples=10", **extra_kwargs) == snapshot_cli
