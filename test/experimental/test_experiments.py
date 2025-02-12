import threading

import pytest
from pytest import ExitCode

from schemathesis.experimental import ENV_PREFIX, POSITIVE_DATA_ACCEPTANCE, ExperimentSet


def test_experiments():
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", "FOO", "", "")

    del experiments

    assert not example.is_enabled
    example.enable()
    assert example.is_enabled
    example.disable()
    assert not example.is_enabled


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((f"--experimental={POSITIVE_DATA_ACCEPTANCE.label}",), {}),
        ((), {"env": {POSITIVE_DATA_ACCEPTANCE.env_var: "true"}}),
    ],
)
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_enable_via_cli(cli, schema_url, args, kwargs):
    result = cli.run(schema_url, *args, **kwargs)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "EXPERIMENTS" in result.stdout
    assert POSITIVE_DATA_ACCEPTANCE.is_enabled


def test_enable_via_env_var(monkeypatch):
    env_var = "FOO"
    monkeypatch.setenv(f"{ENV_PREFIX}_{env_var}", "true")
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", env_var, "", "")
    assert example.is_enabled


@pytest.mark.parametrize("is_enabled", [True, False])
def test_multiple_threads(is_enabled):
    experiments = ExperimentSet()
    example = experiments.create_experiment("Example", "FOO", "", "")
    if is_enabled:
        example.enable()
    error = None

    def check_enabled():
        nonlocal error
        try:
            assert example.is_enabled == is_enabled
        except Exception as exc:
            error = exc

    thread = threading.Thread(target=check_enabled)
    thread.start()
    thread.join()
    assert error is None
