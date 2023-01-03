import json

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from pytest import ExitCode

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


@pytest.mark.usefixtures("reset_hooks")
def test_cli(testdir, raw_schema, cli, openapi3_base_url, hypothesis_max_examples):
    module = testdir.make_importable_pyfile(
        hook="""
    import schemathesis

    seen = set()

    @schemathesis.register_check
    def unique_test_cases(response, case):
        command = case.as_curl_command({"X-Schemathesis-TestCaseId": "0"})
        assert command not in seen, "Test case already seen!"
        seen.add(command)
    """
    )
    schema_file = testdir.makefile(".json", schema=json.dumps(raw_schema))
    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        "-cunique_test_cases",
        f"--hypothesis-max-examples={hypothesis_max_examples or 30}",
        "--data-generation-unique",
        "--data-generation-method=all",
        "--hypothesis-phases=generate",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
