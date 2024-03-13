import platform

import pytest
from hypothesis import HealthCheck, Phase, given, settings

import schemathesis
from schemathesis import DataGenerationMethod, contrib


@pytest.fixture
def unique_data():
    contrib.unique_data.install()
    yield
    contrib.unique_data.uninstall()


@pytest.fixture(
    params=[
        {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "foo": {"type": "string"},
                    },
                }
            }
        },
        {"application/json": {"schema": {"type": "integer"}}},
        {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "data": {"type": "string", "format": "binary"},
                    },
                    "required": ["data"],
                }
            }
        },
        None,
    ]
)
def raw_schema(request, empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/data/{path_param}/": {
            "get": {
                "parameters": [
                    {
                        "name": f"{location}_param",
                        "in": location,
                        "required": True,
                        "schema": {"type": "string"},
                        **kwargs,
                    }
                    for location, kwargs in (
                        ("path", {}),
                        ("query", {"style": "simple", "explode": True}),
                        ("header", {}),
                        ("cookie", {}),
                    )
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    if request.param is not None:
        empty_open_api_3_schema["paths"]["/data/{path_param}/"]["get"].update(
            {
                "requestBody": {
                    "content": request.param,
                    "required": True,
                }
            }
        )
    return empty_open_api_3_schema


@pytest.mark.hypothesis_nested
@pytest.mark.xfail(True, reason="The ``--contrib-unique-data`` feature is deprecated and unstable", strict=False)
def test_python_tests(unique_data, raw_schema, hypothesis_max_examples):
    schema = schemathesis.from_dict(raw_schema)
    endpoint = schema["/data/{path_param}/"]["GET"]
    seen = set()

    @given(
        case=endpoint.as_strategy(data_generation_method=DataGenerationMethod.positive)
        | endpoint.as_strategy(data_generation_method=DataGenerationMethod.negative)
    )
    @settings(
        max_examples=hypothesis_max_examples or 30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
        phases=[Phase.generate],
        deadline=None,
    )
    def test(case):
        # Check uniqueness by the generated cURL command as a different way to check it
        command = case.as_curl_command({"X-Schemathesis-TestCaseId": "0"})
        assert command not in seen, command
        seen.add(command)

    test()


@pytest.fixture
def unique_hook(testdir):
    return testdir.make_importable_pyfile(
        hook="""
        import schemathesis

        @schemathesis.check
        def unique_test_cases(response, case):
            if not hasattr(case.operation.schema, "seen"):
                case.operation.schema.seen = set()
            command = case.as_curl_command({"X-Schemathesis-TestCaseId": "0"})
            assert command not in case.operation.schema.seen, f"Test case already seen! {command}"
            case.operation.schema.seen.add(command)
        """
    )


def run(testdir, cli, unique_hook, schema, openapi3_base_url, hypothesis_max_examples, *args):
    schema_file = testdir.make_openapi_schema_file(schema)
    return cli.main(
        "run",
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        "-cunique_test_cases",
        f"--hypothesis-max-examples={hypothesis_max_examples or 30}",
        "--contrib-unique-data",
        "--data-generation-method=all",
        "--hypothesis-suppress-health-check=filter_too_much",
        "--hypothesis-phases=generate",
        *args,
        hooks=unique_hook.purebasename,
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
@pytest.mark.xfail(True, reason="The ``--contrib-unique-data`` feature is deprecated and unstable", strict=False)
@pytest.mark.snapshot(replace_statistic=True)
def test_cli(testdir, unique_hook, raw_schema, cli, openapi3_base_url, hypothesis_max_examples, snapshot_cli):
    assert run(testdir, cli, unique_hook, raw_schema, openapi3_base_url, hypothesis_max_examples) == snapshot_cli


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.xfail(True, reason="The ``--contrib-unique-data`` feature is deprecated and unstable", strict=False)
def test_explicit_headers(
    testdir,
    unique_hook,
    empty_open_api_3_schema,
    cli,
    openapi3_base_url,
    hypothesis_max_examples,
    workers,
    snapshot_cli,
):
    header_name = "X-Session-ID"
    empty_open_api_3_schema["paths"] = {
        "/success": {
            "get": {
                "parameters": [
                    {
                        "name": name,
                        "in": location,
                        "required": True,
                        "schema": {"type": "string"},
                    }
                    for name, location in (
                        (header_name, "header"),
                        ("key", "query"),
                    )
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    # When explicit headers are passed to CLI
    # And they match one of the parameters
    # Then they should be included in the uniqueness check
    assert (
        run(
            testdir,
            cli,
            unique_hook,
            empty_open_api_3_schema,
            openapi3_base_url,
            hypothesis_max_examples,
            f"-H {header_name}: fixed",
            f"--workers={workers}",
        )
        == snapshot_cli
    )
