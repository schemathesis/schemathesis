from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import schemathesis.cli
from test.apps.builders import make_flask_app

from .fixtures import make_fixture_app


@pytest.fixture(scope="session")
def analyzer_ndjson(app_runner, tmp_path_factory) -> Path:
    # Session-scoped: the NDJSON is read-only, every test consumes the same shard.
    app, _ = make_flask_app(make_fixture_app.PATHS)
    make_fixture_app.attach_handlers(app)
    url = app_runner.openapi_url(app)

    directory = tmp_path_factory.mktemp("analyzer")
    out = directory / "run.ndjson"
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.chdir(directory)
        CliRunner().invoke(
            schemathesis.cli.schemathesis,
            [
                "run",
                url,
                f"--report-ndjson-path={out}",
                "--max-examples=8",
                "--seed=1",
                "--mode=all",
                "--generation-database=none",
            ],
        )
    return out
