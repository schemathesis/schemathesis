import pytest

from test.utils import load_yaml_or_fail

RESPONSES = {"responses": {"default": {"description": "OK"}}}
SCHEMA = {
    "/a": {
        "post": {
            "tags": ["Example"],
            **RESPONSES,
        }
    },
    "/b": {
        "get": {
            "tags": ["Example"],
            **RESPONSES,
        }
    },
    "/c": {
        "get": RESPONSES,
    },
    "/d": {
        "get": {
            "tags": ["Example", "Other"],
            **RESPONSES,
        }
    },
}


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


@pytest.mark.parametrize(
    ["args", "expected"],
    (
        (
            {},
            ["POST /a", "GET /b", "GET /c", "GET /d"],
        ),
        (
            {"include-tag": "Example", "exclude-method": "POST"},
            ["GET /b", "GET /d"],
        ),
        (
            {"include-method": "POST"},
            ["POST /a"],
        ),
        (
            {"include-method": "GET"},
            ["GET /b", "GET /c", "GET /d"],
        ),
        (
            {"exclude-tag": "Example"},
            ["GET /c"],
        ),
        (
            {"exclude-tag": "Example"},
            ["GET /c"],
        ),
    ),
)
def test_filters_with_cli_options(ctx, cli, args, expected, cassette_path):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(SCHEMA)

    assert_filtered(
        cli,
        schema_path,
        cassette_path,
        f"{api.base_url}/api",
        expected,
        args=[f"--{key}={value}" for key, value in args.items()],
        kwargs={},
    )


@pytest.mark.parametrize(
    ["args", "expected"],
    (
        # Disable all NOT POST operations
        (
            [{"exclude-method": "POST"}],
            ["POST /a"],
        ),
        # Disable all operations tagged with "Example"
        (
            [{"include-tag": "Example"}],
            ["GET /c"],
        ),
        # Disable only `GET /b` explicitly
        (
            [{"include-name": "GET /b"}],
            ["POST /a", "GET /c", "GET /d"],
        ),
        # Disable everything NOT tagged Example
        (
            [{"exclude-tag": "Example"}],
            ["POST /a", "GET /b", "GET /d"],
        ),
    ),
)
def test_filters_with_config(ctx, cli, args, expected, cassette_path):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(SCHEMA)

    assert_filtered(
        cli,
        schema_path,
        cassette_path,
        f"{api.base_url}/api",
        expected,
        args=[],
        kwargs={"config": {"operations": [{**arg, "enabled": False} for arg in args]}},
    )


@pytest.mark.parametrize(
    "cli_args, config, expected",
    [
        # CLI includes only GET, Config disables everything not tagged 'Example'
        (
            {"include-method": "GET"},
            [{"exclude-tag": "Example"}],
            ["GET /b", "GET /d"],
        ),
        # CLI excludes POST, config disables only `GET /b`
        (
            {"exclude-method": "POST"},
            [{"include-name": "GET /b"}],
            ["GET /c", "GET /d"],
        ),
        # CLI includes only POST, config disables everything NOT tagged Example
        (
            {"include-method": "POST"},
            [{"exclude-tag": "Example"}],
            ["POST /a"],
        ),
        # CLI includes only GET, config disables tag=Other
        (
            {"include-method": "GET"},
            [{"include-tag": "Other"}],
            ["GET /b", "GET /c"],
        ),
    ],
)
def test_cli_and_config_intersection(ctx, cli, cli_args, config, expected, cassette_path):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(SCHEMA)

    assert_filtered(
        cli,
        schema_path,
        cassette_path,
        f"{api.base_url}/api",
        expected,
        args=[f"--{key}={value}" for key, value in cli_args.items()],
        kwargs={"config": {"operations": [{**item, "enabled": False} for item in config]}},
    )


def assert_filtered(cli, schema_path, cassette_path, base_url, expected, *, args, kwargs):
    result = cli.run(
        str(schema_path),
        "--checks=not_a_server_error",
        "--max-examples=1",
        "--phases=fuzzing",
        f"--url={base_url}",
        f"--report-vcr-path={cassette_path}",
        *args,
        **kwargs,
    )
    assert result.exit_code == 0, result.stdout
    cassette = load_yaml_or_fail(cassette_path, context=f"stdout:\n{result.stdout}")
    interactions = cassette.get("http_interactions") or []
    actual = [f"{entry['request']['method']} /{entry['request']['uri'].split('/')[-1]}" for entry in interactions]
    assert actual == expected, f"stdout:\n{result.stdout}\ncassette:\n{cassette}"


def _run_with_filters(cli, ctx, api, *filters):
    return cli.run(
        str(ctx.openapi.write_schema(SCHEMA)),
        "--checks=not_a_server_error",
        "--max-examples=1",
        "--phases=fuzzing",
        f"--url={api.base_url}/api",
        *filters,
    )


@pytest.mark.parametrize(
    "filters",
    [
        ["--include-name=GET /nope"],
        ["--include-name=GET /b", "--include-name=GET /nope"],
        ["--include-name=GET /b", "--include-name=POST /a"],
        ["--exclude-name=GET /nope"],
        ["--include-name=GET /b", "--exclude-name=GET /b"],
        ["--include-name-regex=^[a-z]+ZZZ$"],
    ],
    ids=[
        "dead-include-selects-nothing",
        "dead-include-beside-a-live-one",
        "every-include-matches",
        "dead-exclude",
        "includes-and-excludes-cancel-out",
        "dead-regex-shown-verbatim",
    ],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unmatched_filters(ctx, cli, snapshot_cli, filters):
    api = ctx.openapi.apps.success()
    assert _run_with_filters(cli, ctx, api, *filters) == snapshot_cli


@pytest.mark.parametrize("name", ["Query.getNope", "Query.getBooks"], ids=["dead-filter", "live-filter"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unmatched_filters_for_graphql(ctx, cli, snapshot_cli, name):
    api = ctx.graphql.apps.books()
    assert cli.run(api.schema_url, "--max-examples=1", "--mode=positive", f"--include-name={name}") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_operation_id_filter_on_graphql(ctx, cli, snapshot_cli):
    # GraphQL has no `operationId`, so such a filter matches nothing instead of erroring.
    api = ctx.graphql.apps.books()
    result = cli.run(
        api.schema_url,
        "--max-examples=1",
        "--mode=positive",
        "--include-name=Query.getBooks",
        "--exclude-operation-id=nope",
    )
    assert "Traceback" not in result.stdout, result.stdout
    assert result == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unmatched_operations_config_block(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert (
        cli.run(
            str(ctx.openapi.write_schema(SCHEMA)),
            "--checks=not_a_server_error",
            "--max-examples=1",
            "--phases=fuzzing",
            f"--url={api.base_url}/api",
            config={"operations": [{"include-name": "GET /nope", "headers": {"X-Key": "42"}}]},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unmatched_filter_suggests_the_operation_it_nearly_names(ctx, cli, snapshot_cli):
    api = ctx.graphql.apps.books()
    assert (
        cli.run(api.schema_url, "--max-examples=1", "--mode=positive", "--include-name=Query.getBook") == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_combined_regex_filters_are_reported_as_one(ctx, cli, snapshot_cli):
    # Every `*-regex` option builds a single filter whose criteria must all hold.
    api = ctx.openapi.apps.success()
    assert (
        _run_with_filters(cli, ctx, api, "--include-name-regex=^ZZZ$", "--include-method-regex=^QQQ$") == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_no_report_when_an_operation_is_skipped_while_walking(ctx, cli, snapshot_cli):
    # An empty definition is tested but not counted, so a filter cannot be called dead on this schema.
    api = ctx.openapi.apps.success()
    assert (
        cli.run(
            str(ctx.openapi.write_schema({**SCHEMA, "/empty": {"get": {}}})),
            "--checks=not_a_server_error",
            "--max-examples=1",
            "--phases=fuzzing",
            f"--url={api.base_url}/api",
            "--include-name=GET /nope",
        )
        == snapshot_cli
    )
