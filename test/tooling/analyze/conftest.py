from __future__ import annotations

from pathlib import Path

import pytest

from .fixtures import make_fixture_app


@pytest.fixture
def analyzer_ndjson(ctx, app_runner, cli, tmp_path) -> Path:
    app, _ = ctx.openapi.make_flask_app(make_fixture_app.PATHS)
    make_fixture_app.attach_handlers(app)
    port = app_runner.run_flask_app(app)

    out = tmp_path / "run.ndjson"
    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={out}",
        "--max-examples=8",
        "--seed=1",
        "--mode=all",
    )
    return out
