from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import schemathesis.cli
from test.apps.builders import make_flask_app

from .fixtures import make_fixture_app


@pytest.fixture
def write_ndjson():
    def _write(path, lines):
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    return _write


@pytest.fixture(scope="session")
def analyzer_ndjson(app_runner, tmp_path_factory) -> Path:
    # Session-scoped: the NDJSON is read-only, every test consumes the same shard.
    app, _ = make_flask_app(make_fixture_app.PATHS)
    make_fixture_app.attach_handlers(app)
    port = app_runner.run_flask_app(app)

    out = tmp_path_factory.mktemp("analyzer") / "run.ndjson"
    CliRunner().invoke(
        schemathesis.cli.schemathesis,
        [
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            f"--report-ndjson-path={out}",
            "--max-examples=8",
            "--seed=1",
            "--mode=all",
            "--generation-database=none",
        ],
    )
    return out
