from __future__ import annotations

import pytest
import tomli_w
from _pytest.main import ExitCode
from click.testing import CliRunner

import schemathesis.cli
from schemathesis.core.hooks import HOOKS_MODULE_ENV_VAR


@pytest.fixture
def cli(tmp_path, app_runner):
    """CLI runner helper.

    Provides in-process execution via `click.CliRunner`.
    """
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
