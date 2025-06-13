import pytest
import yaml

RESPONSES = {"responses": {"default": {"description": "OK"}}}


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


def load_cassette(path):
    with path.open(encoding="utf-8") as fd:
        return yaml.safe_load(fd)


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
def test_filters(ctx, cli, args, expected, cassette_path, openapi3_base_url):
    schema_path = ctx.openapi.write_schema(
        {
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
    )

    assert_filtered(
        cli,
        schema_path,
        cassette_path,
        openapi3_base_url,
        expected,
        args=[f"--{key}={value}" for key, value in args.items()],
        kwargs={},
    )


def assert_filtered(cli, schema_path, cassette_path, openapi3_base_url, expected, *, args, kwargs):
    cli.run(
        str(schema_path),
        "--checks=not_a_server_error",
        "--max-examples=1",
        "--phases=fuzzing",
        f"--url={openapi3_base_url}",
        f"--report-vcr-path={cassette_path}",
        *args,
        **kwargs,
    )
    cassette = load_cassette(cassette_path)
    assert [
        f"{entry['request']['method']} /{entry['request']['uri'].split('/')[-1]}"
        for entry in cassette["http_interactions"]
    ] == expected
