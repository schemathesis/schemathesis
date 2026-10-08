def test_html_report_header(ctx, cli, tmp_path, snapshot_html):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=1",
        "--phases=fuzzing",
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(tmp_path)},
    )
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == snapshot_html
    assert (tmp_path / "assets" / "report.css").is_file()
