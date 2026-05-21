from __future__ import annotations

import json
from pathlib import Path

from flask import jsonify

from schemathesis.reporting.crashes import MANIFEST_FILENAME


def _crash_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in directory.iterdir() if path.suffix == ".json" and path.name != MANIFEST_FILENAME]


def _crashes_dir(tmp_path: Path) -> Path:
    return tmp_path / ".schemathesis" / "default" / "cache" / "crashes"


def test_crash_file_written_on_failure(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/boom": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "500": {"description": "Error"},
                    }
                }
            }
        }
    )

    @app.route("/boom")
    def boom():
        return jsonify({"error": "crash"}), 500

    cli.run_openapi_app(app, "--max-examples=1")

    crash_files = _crash_files(_crashes_dir(tmp_path))
    assert len(crash_files) == 1, crash_files

    crash = json.loads(crash_files[0].read_text())
    step = crash["sequence"][0]
    assert (
        crash["operation"],
        step["method"],
        step["response"]["status_code"],
        [c["name"] for c in step["checks"]],
    ) == ("GET /boom", "GET", 500, ["not_a_server_error"])


def test_no_crash_file_when_cache_disabled(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return jsonify({"error": "crash"}), 500

    cli.run_openapi_app(app, "--max-examples=1", config={"cache": {"enabled": False}})

    assert not _crash_files(_crashes_dir(tmp_path))


def test_no_crash_file_on_success(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app({"/ok": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/ok")
    def ok():
        return jsonify({})

    cli.run_openapi_app(app, "--max-examples=1")

    assert not _crash_files(_crashes_dir(tmp_path))
