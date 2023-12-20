import pytest


@pytest.mark.operations("success")
def test_fills_missing_examples(cli, openapi3_schema_url, snapshot_cli):
    assert (
        cli.run(openapi3_schema_url, "--hypothesis-phases=explicit", "--contrib-openapi-fill-missing-examples")
        == snapshot_cli
    )
