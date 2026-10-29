import subprocess
import sys

import pytest

from test.apps.catalog.grpc.descriptors import users_descriptor_set, write_descriptor_set

pytest.importorskip("grpc", reason="grpcio has no free-threaded build")


@pytest.mark.parametrize("source", ["reflection", "descriptor-set"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_grpc_run_finds_server_error(ctx, cli, tmp_path, snapshot_cli, source):
    if source == "reflection":
        server = ctx.grpc.apps.users()
        args = [server.target]
    else:
        server = ctx.grpc.apps.users(reflection=None)
        write_descriptor_set(tmp_path, users_descriptor_set())
        args = ["api.binpb", f"--url={server.target}"]
    assert cli.run(*args, "--max-examples=10", "--phases=fuzzing", "--seed=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_descriptor_set_requires_url(cli, tmp_path, snapshot_cli):
    write_descriptor_set(tmp_path, users_descriptor_set())
    assert cli.run("api.binpb") == snapshot_cli


@pytest.mark.parametrize("report", ["har", "vcr"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_http_reports_refused(cli, tmp_path, snapshot_cli, report):
    # Refused before connecting, so an unreachable target gets the same answer.
    assert cli.run("grpc://127.0.0.1:1", f"--report={report}") == snapshot_cli
    assert not (tmp_path / "schemathesis-report").exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_remote_descriptor_set_extension_is_not_grpc(cli, snapshot_cli):
    assert cli.run("http://127.0.0.1:1/api.pb") == snapshot_cli


@pytest.mark.parametrize("option", ["--tls-verify=false", "--proxy=http://127.0.0.1:1"], ids=["tls-verify", "proxy"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unusable_options_refused(ctx, cli, snapshot_cli, option):
    server = ctx.grpc.apps.users(tls=True)
    assert cli.run(server.target, option) == snapshot_cli


BLOCK_PROTOBUF_AND_RUN = """
import sys
sys.modules["google.protobuf"] = None
from schemathesis.cli import schemathesis
schemathesis(["run", "grpc://127.0.0.1:1"])
"""


def test_missing_grpc_extra():
    result = subprocess.run([sys.executable, "-c", BLOCK_PROTOBUF_AND_RUN], capture_output=True, text=True)
    assert (result.returncode, result.stderr.splitlines()[-1]) == (
        1,
        "Error: Loading gRPC schemas requires the `grpc` extra: `pip install schemathesis[grpc]`",
    )
