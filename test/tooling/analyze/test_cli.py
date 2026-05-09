import json

from click.testing import CliRunner

from scripts.analyze.__main__ import cli


def _invoke(*args):
    return CliRunner().invoke(cli, list(args))


def test_cli_writes_metrics_json_and_report_md(analyzer_ndjson, tmp_path):
    out = tmp_path / "out"
    result = _invoke(str(analyzer_ndjson), "-o", str(out))
    assert result.exit_code == 0, result.output
    assert (out / "metrics.json").exists()
    assert (out / "report.md").exists()
    metrics = json.loads((out / "metrics.json").read_text())
    assert {"buckets", "operations", "phases", "failures", "mutations", "rates", "reachability"} <= set(metrics)
    assert "# Schemathesis run report" in (out / "report.md").read_text()


def test_cli_missing_file_exits_2(tmp_path):
    out = tmp_path / "out"
    result = _invoke(str(tmp_path / "nope.ndjson"), "-o", str(out))
    assert result.exit_code == 2
    assert "nope" in result.stderr


def test_cli_partial_ndjson_skips_with_warning(tmp_path):
    path = tmp_path / "partial.ndjson"
    path.write_text(
        "\n".join(
            [
                json.dumps({"Initialize": {"command": "x", "schemathesis_version": "test", "seed": 0}}),
                "this-is-not-json{{",
            ]
        )
        + "\n"
    )
    out = tmp_path / "out"
    result = _invoke(str(path), "-o", str(out))
    assert result.exit_code == 0, result.output
    assert "skipping malformed line" in result.stderr
    assert (out / "metrics.json").exists()


def test_cli_creates_output_directory(analyzer_ndjson, tmp_path):
    out = tmp_path / "deeply" / "nested" / "out"
    result = _invoke(str(analyzer_ndjson), "-o", str(out))
    assert result.exit_code == 0, result.output
    assert out.is_dir()
    assert (out / "metrics.json").exists()


def test_cli_charts_flag_writes_charts_subdir(analyzer_ndjson, tmp_path):
    out = tmp_path / "out"
    result = _invoke(str(analyzer_ndjson), "-o", str(out), "--charts")
    assert result.exit_code == 0, result.output
    assert (out / "charts" / "buckets.png").exists()
