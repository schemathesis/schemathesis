import pytest
from pytest import ExitCode

from schemathesis.experimental import OPEN_API_3_1, ExperimentSet, ENV_PREFIX


def test_experiments():
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", "", "FOO", "", "")

    del experiments

    assert not example.is_enabled
    example.enable()
    assert example.is_enabled
    example.disable()
    assert not example.is_enabled


@pytest.mark.parametrize(
    "args, kwargs",
    (
        ((f"--experimental={OPEN_API_3_1.name}",), {}),
        ((), {"env": {OPEN_API_3_1.env_var: "true"}}),
    ),
)
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_enable_via_cli(cli, schema_url, args, kwargs):
    result = cli.run(schema_url, *args, **kwargs)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "Experimental Features:" in result.stdout
    assert OPEN_API_3_1.is_enabled


def test_enable_via_env_var(monkeypatch):
    env_var = "FOO"
    monkeypatch.setenv(f"{ENV_PREFIX}_{env_var}", "true")
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", "", env_var, "", "")
    assert example.is_enabled
