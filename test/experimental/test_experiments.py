import pytest
from pytest import ExitCode

from schemathesis.experimental import OPEN_API_3_1, ExperimentSet


def test_experiments():
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", "", "", "")

    del experiments

    assert not example.is_enabled
    example.enable()
    assert example.is_enabled
    example.disable()
    assert not example.is_enabled


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_enable_via_cli(cli, schema_url):
    result = cli.run(schema_url, f"--experimental={OPEN_API_3_1.name}")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "Experimental Features:" in result.stdout
    assert OPEN_API_3_1.is_enabled
