import platform
from xml.etree import ElementTree

import pytest
import yaml
from _pytest.main import ExitCode

from test.utils import flaky


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_default(cli, schema_url, snapshot_cli, workers):
    assert (
        cli.run(
            schema_url,
            "--max-examples=80",
            "-c not_a_server_error",
            f"--workers={workers}",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_sanitization(cli, schema_url, tmp_path):
    cassette_path = tmp_path / "output.yaml"
    token = "secret"
    result = cli.run(
        schema_url,
        "--phases=stateful",
        "--max-examples=80",
        "-c not_a_server_error",
        f"--header=Authorization: Bearer {token}",
        f"--report-vcr-path={cassette_path}",
        "--max-failures=1",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
@flaky(max_runs=5, min_passes=1)
def test_max_failures(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--max-examples=80",
            "--max-failures=2",
            "--generation-database=none",
            "-c not_a_server_error",
            "--phases=fuzzing,stateful",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_with_cassette(tmp_path, cli, schema_url):
    cassette_path = tmp_path / "output.yaml"
    cli.run(
        schema_url,
        "--max-examples=40",
        "--max-failures=1",
        "-c not_a_server_error",
        f"--report-vcr-path={cassette_path}",
    )
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        cassette = yaml.safe_load(fd)
    assert len(cassette["http_interactions"]) >= 20
    assert cassette["seed"] not in (None, "None")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_junit(tmp_path, cli, schema_url):
    junit_path = tmp_path / "junit.xml"
    result = cli.run(
        schema_url,
        "--phases=stateful",
        "--max-examples=80",
        "--max-failures=1",
        "-c not_a_server_error",
        f"--report-junit-path={junit_path}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert junit_path.exists()
    tree = ElementTree.parse(junit_path)
    root = tree.getroot()
    assert root.tag == "testsuites"
    assert len(root) == 1
    assert len(root[0]) == 1
    assert root[0][0].attrib["name"] == "Stateful tests"
    assert len(root[0][0]) == 1
    assert root[0][0][0].tag == "failure"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_only(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "-n 80",
            "-c not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_stateful_only_with_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--url=http://127.0.0.1:1/api",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_filtered_out(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--max-examples=40",
            "--include-path=/success",
            "--max-failures=1",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--proxy=http://127.0.0.1",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_generation_config(cli, mocker, schema_url, snapshot_cli):
    from schemathesis.specs.openapi import _hypothesis

    mocked = mocker.spy(_hypothesis, "from_schema")
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "--max-examples=50",
            "--generation-allow-x00=false",
            "--generation-codec=ascii",
            "--generation-with-security-parameters=false",
            "-c not_a_server_error",
        )
        == snapshot_cli
    )
    from_schema_kwargs = mocked.call_args_list[0].kwargs
    assert from_schema_kwargs["allow_x00"] is False
    assert from_schema_kwargs["codec"] == "ascii"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_keyboard_interrupt(cli, mocker, schema_url, snapshot_cli):
    def mocked(*args, **kwargs):
        raise KeyboardInterrupt

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    assert cli.run(schema_url, "--phases=stateful") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_missing_link(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--phases=stateful") == snapshot_cli


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_not_enough_links(cli, schema_url, snapshot_cli):
    assert cli.run(schema_url, "--phases=stateful", "--include-method=POST") == snapshot_cli


def test_invalid_parameter_reference(app_factory, app_runner, cli, snapshot_cli):
    # When a link references a non-existent parameter
    app = app_factory(invalid_parameter=True)
    port = app_runner.run_flask_app(app)
    assert cli.run(f"http://127.0.0.1:{port}/openapi.json", "--phases=stateful", "-n 1") == snapshot_cli


def test_missing_body_parameter(app_factory, app_runner, cli, snapshot_cli):
    app = app_factory(omit_required_field=True)
    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "-n 30",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@flaky(max_runs=3, min_passes=1)
@pytest.mark.parametrize("content", ["", "User data as plain text"])
def test_non_json_response(app_factory, app_runner, cli, snapshot_cli, content):
    app = app_factory(return_plain_text=content)
    port = app_runner.run_flask_app(app)
    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "-n 80",
            "--generation-database=none",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_unique_inputs(ctx, cli, snapshot_cli, openapi3_base_url):
    # See GH-2977
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "post": {
                    "responses": {
                        "200": {
                            "links": {"getItem": {"operationId": "GetById"}},
                        }
                    }
                }
            },
            "/item/{id}": {
                "get": {
                    "operationId": "GetById",
                    "responses": {"200": {"descrionn": "Ok"}},
                },
            },
        }
    )
    assert (
        cli.run(
            str(schema_path),
            f"--url={openapi3_base_url}",
            "--phases=stateful",
            "--generation-unique-inputs",
            "--max-examples=10",
        )
        == snapshot_cli
    )
