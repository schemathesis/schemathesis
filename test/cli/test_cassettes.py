import base64

import pytest
import yaml
from _pytest.main import ExitCode

from schemathesis.cli.cassettes import get_command_representation


@pytest.mark.endpoints("success", "upload_file")
def test_store_cassette(cli, schema_url, tmp_path):
    cassette_path = tmp_path / "output.yaml"
    result = cli.run(
        schema_url, f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", "--hypothesis-seed=1"
    )
    assert result.exit_code == ExitCode.OK
    with cassette_path.open() as fd:
        cassette = yaml.safe_load(fd)
    assert len(cassette["http_interactions"]) == 3
    assert cassette["http_interactions"][0]["id"] == "0"
    assert cassette["http_interactions"][1]["id"] == "1"
    assert cassette["http_interactions"][0]["status"] == "SUCCESS"
    assert cassette["http_interactions"][0]["seed"] == "1"
    assert float(cassette["http_interactions"][0]["elapsed"]) >= 0
    data = base64.b64decode(cassette["http_interactions"][0]["response"]["body"]["base64_string"])
    assert data == b'{"success": true}'


def test_get_command_representation_unknown():
    assert get_command_representation() == "<unknown entrypoint>"


def test_get_command_representation(mocker):
    mocker.patch("schemathesis.cli.cassettes.sys.argv", ["schemathesis", "run", "http://example.com/schema.yaml"])
    assert get_command_representation() == "schemathesis run http://example.com/schema.yaml"


@pytest.mark.endpoints("success")
def test_run_subprocess(testdir, tmp_path, schema_url):
    cassette_path = tmp_path / "output.yaml"
    result = testdir.run(
        "schemathesis", "run", f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", schema_url
    )
    assert result.ret == ExitCode.OK
    assert result.outlines[17] == f"Network log: {cassette_path}"
    with cassette_path.open() as fd:
        cassette = yaml.safe_load(fd)
    assert len(cassette["http_interactions"]) == 1
    command = f"schemathesis run --store-network-log={cassette_path} --hypothesis-max-examples=2 {schema_url}"
    assert cassette["command"] == command
