import platform

import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.generation import GenerationMode
from test.utils import flaky


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
def raw_schema(ctx, request):
    schema = ctx.openapi.build_schema(
        {
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
    )
    if request.param is not None:
        schema["paths"]["/data/{path_param}/"]["get"].update(
            {
                "requestBody": {
                    "content": request.param,
                    "required": True,
                }
            }
        )
    return schema


@pytest.fixture
def unique_hook(ctx):
    with ctx.check(
        """
@schemathesis.check
def unique_test_cases(ctx, response, case):
    if not hasattr(case.operation.schema, "seen"):
        case.operation.schema.seen = set()
    command = case.as_curl_command({"X-Schemathesis-TestCaseId": "0"})
    assert command not in case.operation.schema.seen, f"Test case already seen! {command}"
    case.operation.schema.seen.add(command)
"""
    ) as module:
        yield module


def run(ctx, cli, unique_hook, schema, base_url, hypothesis_max_examples, *args):
    schema_file = ctx.makefile(schema)
    return cli.main(
        "run",
        str(schema_file),
        f"--url={base_url}",
        "-cunique_test_cases",
        f"--max-examples={hypothesis_max_examples or 30}",
        "--generation-unique-inputs",
        "--mode=all",
        "--suppress-health-check=filter_too_much",
        "--phases=examples,fuzzing",
        *args,
        hooks=unique_hook,
        # Masked values would make distinct inputs render as the same command.
        config={"warnings": False, "output": {"sanitization": {"enabled": False}}},
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
@flaky(max_runs=3, min_passes=1)
def test_cli(ctx, unique_hook, raw_schema, cli, hypothesis_max_examples, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert run(ctx, cli, unique_hook, raw_schema, f"{api.base_url}/api", hypothesis_max_examples) == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
def test_cli_failure(ctx, unique_hook, cli, hypothesis_max_examples, snapshot_cli):
    api = ctx.openapi.apps.failure()
    assert (
        cli.main(
            "run",
            api.schema_url,
            "-cunique_test_cases",
            "-cnot_a_server_error",
            f"--max-examples={hypothesis_max_examples or 30}",
            "--generation-unique-inputs",
            "--mode=all",
            "--suppress-health-check=filter_too_much",
            "--phases=fuzzing",
            hooks=unique_hook,
        )
        == snapshot_cli
    )


def test_graphql_url(ctx, cli, unique_hook, snapshot_cli):
    api = ctx.graphql.apps.books()
    assert (
        cli.main(
            "run",
            api.schema_url,
            "-cunique_test_cases",
            "--max-examples=5",
            "--generation-unique-inputs",
            hooks=unique_hook,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_explicit_headers(
    ctx,
    unique_hook,
    cli,
    hypothesis_max_examples,
    workers,
    snapshot_cli,
):
    api = ctx.openapi.apps.success()
    header_name = "X-Session-ID"
    schema = ctx.openapi.build_schema(
        {
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
    )
    # When explicit headers are passed to CLI
    # And they match one of the parameters
    # Then they should be included in the uniqueness check
    assert (
        run(
            ctx,
            cli,
            unique_hook,
            schema,
            f"{api.base_url}/api",
            hypothesis_max_examples,
            f"-H {header_name}: fixed",
            f"--workers={workers}",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_positive_multipart_not_reported_as_negative(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.upload_file()
    assert (
        cli.run(
            api.schema_url,
            "--generation-unique-inputs",
            "--mode=positive",
            "--phases=fuzzing",
            "--checks=negative_data_rejection",
            "--max-examples=10",
        )
        == snapshot_cli
    )


def test_hash_keeps_positive_multipart_case(ctx):
    operation = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "data": {"type": "string", "format": "binary"},
                                        "price": {"type": "number"},
                                    },
                                    "required": ["data", "price"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )["/upload"]["POST"]

    @given(operation.as_strategy(generation_mode=GenerationMode.POSITIVE))
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None)
    def check(case):
        body = dict(case.body)
        hash(case)
        assert (case.body, case.meta.generation.mode) == (body, GenerationMode.POSITIVE)
        assert [type(value) for value in case.body.values()] == [type(value) for value in body.values()]

    check()


def test_sensitive_parameter_values_are_distinct_inputs(ctx, cli):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.build_schema(
        {
            "/success": {
                "get": {
                    "parameters": [{"name": "key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    cli.main(
        "run",
        str(ctx.makefile(schema)),
        f"--url={api.base_url}/api",
        "--generation-unique-inputs",
        "--mode=positive",
        "--phases=fuzzing",
        "--checks=not_a_server_error",
        "--max-examples=10",
        config={"warnings": False},
    )
    assert len({request.raw_query for request in api.calls_to("/api/success")}) > 1
