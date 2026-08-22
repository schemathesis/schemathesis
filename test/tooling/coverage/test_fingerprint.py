from __future__ import annotations

import json

from scripts.coverage import fingerprint as cli_fingerprint
from tools.coverage.fingerprint import diff_rows, fingerprint_schema

_PATHS = {
    "/users/{id}": {
        "get": {
            "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "minimum": 1}},
                {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
            ],
            "responses": {"200": {"description": "OK"}},
        },
    },
    "/users": {
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string", "example": "alice"}},
                        }
                    }
                }
            },
            "responses": {"201": {"description": "Created"}},
        },
    },
}


def test_fingerprint_schema_is_deterministic_and_sorted(ctx):
    raw = ctx.openapi.build_schema(_PATHS)
    first = fingerprint_schema(raw)
    second = fingerprint_schema(raw)
    assert first.rows == second.rows
    assert first.rows == sorted(first.rows)
    assert first.errors == []
    operations = {row[0] for row in first.rows}
    assert {"GET /users/{id}", "POST /users"} <= operations
    assert "DELETE /users" in operations
    assert {row[2] for row in first.rows} == {"positive", "negative"}


def test_fingerprint_digest_covers_request_values(ctx):
    renamed = json.loads(json.dumps(_PATHS).replace('"alice"', '"bob"'))
    baseline = fingerprint_schema(ctx.openapi.build_schema(_PATHS)).rows
    current = fingerprint_schema(ctx.openapi.build_schema(renamed)).rows
    changed = diff_rows(baseline, current)
    assert changed.removed and changed.added
    assert {row[:6] for row in changed.removed} == {row[:6] for row in changed.added}


def test_diff_rows_is_a_multiset_diff():
    row = ("GET /a", "valid_string", "positive", "query", "q", "/", "d1")
    other = ("GET /a", "valid_string", "positive", "query", "q", "/", "d2")
    changed = diff_rows([row, row, other], [row, other, other])
    assert changed.removed == [row]
    assert changed.added == [other]
    assert not diff_rows([row, other], [other, row])


def test_cli_round_trip_reports_diff_and_exit_code(ctx, tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(ctx.openapi.build_schema(_PATHS)))
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"

    assert cli_fingerprint.main(["--spec", str(spec), "--out", str(baseline)]) == 0
    assert cli_fingerprint.main(["--spec", str(spec), "--out", str(current), "--diff", str(baseline)]) == 0
    assert "no differences" in capsys.readouterr().out

    spec.write_text(json.dumps(ctx.openapi.build_schema(_PATHS)).replace('"alice"', '"bob"'))
    assert cli_fingerprint.main(["--spec", str(spec), "--out", str(current), "--diff", str(baseline)]) == 1
    out = capsys.readouterr().out
    assert "POST /users" in out
    assert "- " in out and "+ " in out
