import pytest


@pytest.mark.operations("success")
def test_fills_missing_examples(cli, openapi3_schema_url, snapshot_cli):
    assert (
        cli.run(openapi3_schema_url, "--phases=examples", config={"phases": {"examples": {"fill-missing": True}}})
        == snapshot_cli
    )
