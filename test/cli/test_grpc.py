import subprocess
import sys

import pytest

from test.apps.catalog.grpc.descriptors import (
    all_types_descriptor_set,
    side_effects_descriptor_set,
    users_descriptor_set,
    write_descriptor_set,
)

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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_proto_file_refused(cli, tmp_path, snapshot_cli):
    (tmp_path / "users.proto").write_text('syntax = "proto3";\npackage pkg;\nservice Users {}\n')
    assert cli.run("users.proto", "--url=grpc://127.0.0.1:1") == snapshot_cli


@pytest.mark.parametrize(
    ("user_id", "sent"),
    [
        (1, ["Bearer secret", None]),
        # Only the first successful call is resent without credentials.
        (403, ["Bearer secret", None, "Bearer secret", "Bearer secret"]),
    ],
    ids=["ignored", "enforced"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_ignored_auth(ctx, cli, snapshot_cli, user_id, sent):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        f"""
@schemathesis.hook
def map_body(context, body):
    return {{"userId": {user_id}}}
        """
    )
    assert (
        cli.main(
            "run",
            server.target,
            "--include-name=pkg.Users/GetUser",
            "--max-examples=3",
            "-H",
            "Authorization: Bearer secret",
            hooks=module,
        )
        == snapshot_cli
    )
    assert [dict(call.metadata).get("authorization") for call in server.calls] == sent


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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_streaming_method_selected(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    assert cli.run(server.target, "--include-name=pkg.Users/WatchUsers", "--max-examples=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_authentication_warning_uses_status_names(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 401}
        """
    )
    assert (
        cli.main("run", server.target, "--include-name=pkg.Users/GetUser", "--max-examples=1", hooks=module)
        == snapshot_cli
    )


def test_missing_grpc_extra():
    result = subprocess.run([sys.executable, "-c", BLOCK_PROTOBUF_AND_RUN], capture_output=True, text=True)
    assert (result.returncode, result.stderr.splitlines()[-1]) == (
        1,
        "Error: Loading gRPC schemas requires the `grpc` extra: `pip install schemathesis[grpc]`",
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_any_fields_warning(ctx, cli, tmp_path, snapshot_cli):
    server = ctx.grpc.apps.users()
    write_descriptor_set(tmp_path, all_types_descriptor_set())
    assert (
        cli.run(
            "api.binpb",
            f"--url={server.target}",
            "--include-name=types.Types/Envelope",
            "--max-examples=1",
            "--checks=response_schema_conformance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unserved_operation_warning(ctx, cli, tmp_path, snapshot_cli):
    server = ctx.grpc.apps.users()
    write_descriptor_set(tmp_path, all_types_descriptor_set())
    assert (
        cli.run("api.binpb", f"--url={server.target}", "--include-name=types.Types/Scalars", "--max-examples=10")
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_partly_served_operation(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    # Odd ids are served, even ones are not.
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 1 if body.get("userId", 0) % 2 else 501}
        """
    )
    assert (
        cli.main(
            "run", server.target, "--include-name=pkg.Users/GetUser", "--max-examples=10", "--seed=1", hooks=module
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unreachable_descriptor_set_target(ctx, cli, tmp_path, snapshot_cli):
    server = ctx.grpc.apps.users()
    server.stop()
    write_descriptor_set(tmp_path, users_descriptor_set())
    assert (
        cli.run("api.binpb", f"--url={server.target}", "--include-name=pkg.Users/GetUser", "--max-examples=1")
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_deadline_exceeded(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 13}
        """
    )
    assert (
        cli.main(
            "run",
            server.target,
            "--include-name=pkg.Users/GetUser",
            "--max-examples=1",
            "--request-timeout=0.2",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_missing_test_data_warning(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 404}
        """
    )
    assert (
        cli.main("run", server.target, "--include-name=pkg.Users/GetUser", "--max-examples=1", hooks=module)
        == snapshot_cli
    )


def test_methods_with_side_effects_run_last(ctx, cli, tmp_path):
    server = ctx.grpc.apps.side_effects()
    write_descriptor_set(tmp_path, side_effects_descriptor_set())
    cli.run("api.binpb", f"--url={server.target}", "--max-examples=1", "--workers=2")
    order = list(dict.fromkeys(call.method for call in server.calls))
    # Two workers race within a layer, never across layers.
    assert [set(order[:2]), set(order[2:4]), set(order[4:])] == [
        {"ListItems", "Status"},
        {"Alarm", "Listen"},
        {"DeleteItem", "SetConfig"},
    ]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_reaches_created_names_and_page_tokens(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.library()
    assert (
        cli.run(server.target, "--phases=fuzzing,stateful", "--max-examples=10", "--seed=1", "--warnings=off")
        == snapshot_cli
    )
