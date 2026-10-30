import json

from flask import jsonify


def test_unique_inputs_report_logical_phase_rates_without_resending(ctx, cli, tmp_path):
    values = list(range(1, 15))
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "enum": values},
                            "examples": {str(value): {"value": value} for value in values},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Missing"}},
                }
            }
        }
    )
    calls = []

    @app.get("/items/<int:item_id>")
    def items(item_id):
        calls.append(item_id)
        return jsonify({"id": item_id}), 200 if item_id <= 3 else 404

    report = tmp_path / "report.json"
    # Restrict generated inputs to the finite schema domain; this is a real user hook,
    # not a mocked engine or warning event. Explicit examples first exercise the same inputs.
    with ctx.hook(
        """
from hypothesis import strategies as st

@schemathesis.hook
def before_generate_path_parameters(ctx, strategy):
    return st.sampled_from([{"id": value} for value in range(1, 15)])
"""
    ) as hooks:
        result = cli.run_openapi_app(
            app,
            "--phases=examples,fuzzing",
            "--max-examples=100",
            "--mode=positive",
            "--generation-unique-inputs",
            "--checks=not_a_server_error",
            "--warnings=low_valid_rate",
            "--seed=1",
            f"--report-json-path={report}",
            hooks=hooks,
            config={"cache": {"enabled": False}, "warnings": {"fail-on": ["low_valid_rate"]}},
        )
    assert result.exit_code == 0, result.stdout
    data = json.loads(report.read_text(encoding="utf-8"))
    expected = {"accepted": 3, "unreachable": 11, "conflicts": 0, "rejected": 0}
    assert data["valid_rates"]["GET /items/{id}"]["fuzzing"] == expected
    assert not data["warnings"]["low_valid_rate"]
    assert data["test_cases"]["generated"] == len(calls) == len(set(calls)) == 14
