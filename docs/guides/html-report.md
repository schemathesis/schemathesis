# HTML Report

Generate a self-contained HTML report with a run verdict, timeline, per-operation
pages with failing cases, and reproduction commands.

```bash
uvx schemathesis run https://api.example.com/openapi.json --report html
```

The report is written to `schemathesis-report/html-<timestamp>/` — open
`index.html` in a browser. Use `--report-html-path` to set the directory:

```bash
uvx schemathesis run https://api.example.com/openapi.json \
    --report-html-path my-report
```

Or via `schemathesis.toml`:

```toml
[reports.html]
path = "my-report"
```

## What the Report Shows

The index page shows the overall verdict, a timeline of the run phases, and a table of
tested operations with pass/fail/skip status.

Each operation links to its own page with failing cases, response bodies, and a curl
command to reproduce — click to copy.

The report is self-contained and works offline; attach the directory as a CI artifact.

## Configuration Reference

See the [Reporting section](../reference/configuration.md#reporting) in the Configuration Options reference.
