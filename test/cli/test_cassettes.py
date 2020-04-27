import base64

import pytest
import yaml
from _pytest.main import ExitCode

from schemathesis.cli.cassettes import get_command_representation


@pytest.fixture()
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


def load_cassette(path):
    with path.open() as fd:
        return yaml.safe_load(fd)


@pytest.mark.endpoints("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path):
    result = cli.run(
        schema_url, f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", "--hypothesis-seed=1"
    )
    assert result.exit_code == ExitCode.OK
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 3
    assert cassette["http_interactions"][0]["id"] == "1"
    assert cassette["http_interactions"][1]["id"] == "2"
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
def test_run_subprocess(testdir, cassette_path, schema_url):
    result = testdir.run(
        "schemathesis", "run", f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", schema_url
    )
    assert result.ret == ExitCode.OK
    assert result.outlines[17] == f"Network log: {cassette_path}"
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = f"schemathesis run --store-network-log={cassette_path} --hypothesis-max-examples=2 {schema_url}"
    assert cassette["command"] == command


@pytest.mark.endpoints("invalid")
def test_main_process_error(cli, schema_url, cassette_path):
    # When there is an error in the main process before the background writer is finished
    # Here it is happening because the schema is not valid
    result = cli.run(
        schema_url, f"--store-network-log={cassette_path}", "--hypothesis-max-examples=1", "--hypothesis-seed=1"
    )
    assert result.exit_code == ExitCode.TESTS_FAILED
    # Then there should be no hanging threads
    # And no cassette
    cassette = load_cassette(cassette_path)
    assert cassette is None
