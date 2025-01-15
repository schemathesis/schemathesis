import platform
from xml.etree import ElementTree

import pytest
import yaml
from _pytest.main import ExitCode


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_default(cli, schema_url, snapshot_cli, workers):
    assert (
        cli.run(
            schema_url,
            "--generation-max-examples=80",
            "--exitfirst",
            f"--workers={workers}",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_sanitization(cli, schema_url, tmp_path):
    cassette_path = tmp_path / "output.yaml"
    token = "secret"
    result = cli.run(
        schema_url,
        "--phases=stateful",
        "--generation-max-examples=80",
        f"--header=Authorization: Bearer {token}",
        f"--cassette-path={cassette_path}",
        "--exitfirst",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert token not in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_max_failures(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--generation-max-examples=80",
            "--max-failures=2",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
def test_with_cassette(tmp_path, cli, schema_url):
    cassette_path = tmp_path / "output.yaml"
    cli.run(
        schema_url,
        "--generation-max-examples=40",
        "--exitfirst",
        f"--cassette-path={cassette_path}",
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
        "--generation-max-examples=80",
        "--exitfirst",
        f"--junit-xml={junit_path}",
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
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_stateful_only(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "--generation-max-examples=80",
            "--exitfirst",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_stateful_only_with_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--base-url=http://127.0.0.1:1/api",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_filtered_out(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--generation-max-examples=40",
            "--include-path=/api/success",
            "--exitfirst",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(cli, schema_url, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "--request-proxy=http://127.0.0.1",
            "--phases=stateful",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_generation_config(cli, mocker, schema_url, snapshot_cli):
    from schemathesis.specs.openapi import _hypothesis

    mocked = mocker.spy(_hypothesis, "from_schema")
    assert (
        cli.run(
            schema_url,
            "--phases=stateful",
            "--generation-max-examples=1",
            "--generation-allow-x00=false",
            "--generation-codec=ascii",
            "--generation-with-security-parameters=false",
        )
        == snapshot_cli
    )
    from_schema_kwargs = mocked.call_args_list[0].kwargs
    assert from_schema_kwargs["allow_x00"] is False
    assert from_schema_kwargs["codec"] == "ascii"


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("create_user", "get_user", "update_user", "success")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_stateful_progress=True)
def test_keyboard_interrupt(cli, mocker, schema_url, snapshot_cli):
    def mocked(*args, **kwargs):
        raise KeyboardInterrupt

    mocker.patch("schemathesis.Case.call", wraps=mocked)
    assert cli.run(schema_url, "--phases=stateful") == snapshot_cli
