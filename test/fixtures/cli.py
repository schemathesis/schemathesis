from __future__ import annotations

import pytest
import tomli_w
from _pytest.main import ExitCode
from click.testing import CliRunner

import schemathesis.cli
from schemathesis.core.hooks import HOOKS_MODULE_ENV_VAR

_DEFAULT_GENERATION_DATABASE_ARGUMENT = "--generation-database=none"


def _with_default_generation_database(args, config=None):
    if not args or args[0] != "run":
        return args
    if _has_cli_option(args, "--generation-database") or _has_cli_option(args, "--generation-deterministic"):
        return args
    if _has_generation_config_option(config, "database") or _has_generation_config_option(config, "deterministic"):
        return args
    return (*args, _DEFAULT_GENERATION_DATABASE_ARGUMENT)


def _has_cli_option(args, name: str) -> bool:
    return any(isinstance(arg, str) and (arg == name or arg.startswith(f"{name}=")) for arg in args)


def _has_generation_config_option(config, name: str) -> bool:
    if isinstance(config, dict):
        generation = config.get("generation")
        if isinstance(generation, dict) and name in generation:
            return True
        return any(_has_generation_config_option(value, name) for value in config.values())
    if isinstance(config, list):
        return any(_has_generation_config_option(value, name) for value in config)
    return False


@pytest.fixture
def cli(tmp_path, app_runner, monkeypatch):
    """CLI runner helper.

    Provides in-process execution via `click.CliRunner`. CWD is moved to
    `tmp_path` so any local artifacts the engine writes (e.g.
    `.schemathesis/<project>/cache/`) land inside the test's own directory
    and do not bleed across runs.
    """
    monkeypatch.chdir(tmp_path)
    cli_runner = CliRunner()

    class Runner:
        @staticmethod
        def run_openapi_app(app, *args, path: str = "/openapi.json", **kwargs):
            return Runner.run(app_runner.openapi_url(app, path=path), *args, **kwargs)

        @staticmethod
        def run(*args, **kwargs):
            return Runner.main("run", *args, **kwargs)

        @staticmethod
        def main(*args, config=None, hooks=None, **kwargs):
            args = _with_default_generation_database(args, config)
            if config is not None:
                path = tmp_path / "config.toml"
                path.write_text(tomli_w.dumps(config), encoding="utf-8")
                args = ["--config-file", str(path), *args]
            if hooks is not None:
                env = kwargs.setdefault("env", {})
                env[HOOKS_MODULE_ENV_VAR] = hooks
            result = cli_runner.invoke(schemathesis.cli.schemathesis, args, **kwargs)
            if result.exception and not isinstance(result.exception, SystemExit):
                raise result.exception
            return result

        @staticmethod
        def run_and_assert(*args, exit_code: ExitCode = ExitCode.OK, **kwargs):
            result = Runner.run(*args, **kwargs)
            assert result.exit_code == exit_code, result.stdout
            return result

    return Runner()
