import pytest
from pytest import ExitCode

from schemathesis import fixups


@pytest.fixture(params=["flask_app", "openapi_3_app"])
def app_args(ctx, request, operations):
    if request.param == "flask_app":
        module = ctx.write_pymodule(
            f"""
from test.apps.openapi._flask import create_app

app = create_app({operations})
app.config["prefix_with_bom"] = True
"""
        )
        return f"--app={module}:app", "/schema.yaml"
    app = request.getfixturevalue(request.param)
    app["config"]["prefix_with_bom"] = True
    return (request.getfixturevalue("openapi3_schema_url"),)


@pytest.mark.operations("success")
def test_bom_json(app_args, cli):
    # When server responds with JSON that contains BOM
    # And the `utf8_bom` fixup is enabled
    result = cli.run(*app_args, "--fixups=utf8_bom", "--checks=response_schema_conformance")
    assert fixups.is_installed("utf8_bom")
    # Then the data should be properly decoded
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "Unexpected UTF-8 BOM (decode using utf-8-sig)" not in result.stdout
