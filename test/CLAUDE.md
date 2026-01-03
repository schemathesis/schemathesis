# Testing Guidelines

## Test Hierarchy (prefer in order)
1. **CLI integration tests** - E2E with snapshot assertions (most common in `test/specs/`, `test/cli/`)
2. **Pytest plugin tests** - uses `testdir` fixture (`test/pytest/`, `test/_pytest/`)
3. **Unit tests** - only when integration coverage isn't practical (can be anywhere)

## Running Tests
```bash
pytest test/path/to/test.py -n auto  # parallel execution
pytest test/path/to/test.py::test_name  # single test
```

## Core Patterns

### CLI Tests (preferred)
```python
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_feature(cli, ctx, app_runner, snapshot_cli):
    schema = ctx.openapi.build_schema({"/endpoint": {...}}, components={...})

    app = Flask(__name__)
    # ... define routes ...
    port = app_runner.run_flask_app(app)

    result = cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        "--max-examples=10",  # Use 10 for most tests, avoid 100+
        "--phases=stateful",  # phases: examples, coverage, fuzzing, stateful
    )
    assert result == snapshot_cli
```

### Pytest Plugin Tests
```python
def test_plugin(testdir):
    testdir.make_test("""
@schema.parametrize()
def test_api(case):
    case.call_and_validate()
""", paths={"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)
```

## Style Rules
- **No docstrings** in test functions - use descriptive names
- **Minimal comments** - only explain *why*, not *what*
- **Global imports** - keep imports at module level
- Use `pytest.mark.parametrize(..., ids=[...])` for case labels

## Performance
- **`--max-examples=10`** is sufficient for most tests
- Avoid `--max-examples=100+` unless specifically testing edge cases requiring many iterations
- Tests should complete in seconds, not minutes

## Key Fixtures

### Schema Building
- `ctx.openapi.build_schema()` - construct OpenAPI schemas with components
- `openapi_30`, `openapi_31`, `swagger_20` - schema version fixtures
- `simple_schema`, `complex_schema` - pre-built test schemas

### Test Execution
- `cli` - CLI runner for executing `st run` commands
- `snapshot_cli` - snapshot comparison for CLI output
- `app_runner.run_flask_app()` - run test server, returns port
- `testdir.make_test()` - pytest plugin testing

### Factories
- `response_factory` - create mock HTTP responses
- `case_factory` - create test Case objects

### GraphQL
- `graphql_schema`, `graphql_url` - GraphQL testing fixtures

## Snapshot Testing
- Use `@pytest.mark.snapshot(replace_reproduce_with=True)` for CLI tests
- Update snapshots: `just snapshot-update` or `pytest --snapshot-update`
