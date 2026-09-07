# HTML Report

Generate a static HTML report with the run verdict, failures grouped by check with the
operations they hit, warnings, and errors — the SUMMARY block, shareable as a CI artifact.

```bash
uvx schemathesis run https://api.example.com/openapi.json --report html
```

The report is written to `schemathesis-report/html-<timestamp>/` — open `index.html` in a
browser. Use `--report-html-path` to set the directory:

```bash
uvx schemathesis run https://api.example.com/openapi.json \
    --report-html-path my-report
```

Or via `schemathesis.toml`:

```toml
[reports.html]
path = "my-report"
```

The report is self-contained and works offline; attach the directory as a CI artifact.

## Configuration Reference

See the [Reporting section](../reference/configuration.md#reporting) in the Configuration Options reference.
