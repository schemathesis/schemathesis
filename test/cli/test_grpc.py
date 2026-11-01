import json
import os
import subprocess
import sys

import pytest

from test.apps.catalog.grpc.descriptors import (
    accounts_descriptor_set,
    all_types_descriptor_set,
    side_effects_descriptor_set,
    users_descriptor_set,
    write_descriptor_set,
)
from test.utils import crash_cache_dir, refusing_url

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
def test_coverage_finds_empty_nested_message(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.envelope()
    assert (
        cli.run(
            "api.binpb",
            f"--url={server.target}",
            "--include-name=types.Types/Envelope",
            "--phases=coverage",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_proto_file_refused(cli, tmp_path, snapshot_cli):
    (tmp_path / "users.proto").write_text('syntax = "proto3";\npackage pkg;\nservice Users {}\n')
    assert cli.run("users.proto", "--url=grpc://127.0.0.1:1") == snapshot_cli


@pytest.mark.parametrize(
    ("phase", "user_id", "sent"),
    [
        ("fuzzing", 1, ["Bearer secret", None]),
        # Only the first successful call is resent without credentials.
        ("fuzzing", 403, ["Bearer secret", None, "Bearer secret", "Bearer secret"]),
        # Coverage cases skip the body hook.
        ("coverage", 1, ["Bearer secret", None]),
    ],
    ids=["ignored", "enforced", "coverage"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_ignored_auth(ctx, cli, snapshot_cli, phase, user_id, sent):
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
            f"--phases={phase}",
            "-H",
            "Authorization: Bearer secret",
            hooks=module,
        )
        == snapshot_cli
    )
    assert [dict(call.metadata).get("authorization") for call in server.calls] == sent


# Credentials stay unmasked in the crash file; the edited first step makes its replayed status differ.
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_ignored_auth_replay(ctx, cli, tmp_path, snapshot_cli):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 1}
        """
    )
    cli.main(
        "run",
        server.target,
        "--include-name=pkg.Users/GetUser",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=ignored_auth",
        "--output-sanitize=false",
        "-H",
        "Authorization: Bearer secret",
        hooks=module,
    )
    crash_files = sorted(crash_cache_dir(tmp_path).glob("*.json"))
    crash_file = next(path for path in crash_files if path.name != "manifest.json")
    crash = json.loads(crash_file.read_text())
    crash["sequence"][0]["case_body"]["value"]["userId"] = 404
    crash_file.write_text(json.dumps(crash))
    assert cli.main("replay") == snapshot_cli
    assert sorted(crash_cache_dir(tmp_path).glob("*.json")) == crash_files


@pytest.mark.parametrize("report", ["har", "vcr", "pytest"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_http_reports_refused(cli, tmp_path, snapshot_cli, report):
    # Refused before connecting, so an unreachable target gets the same answer.
    assert cli.run("grpc://127.0.0.1:1", f"--report={report}") == snapshot_cli
    assert not (tmp_path / "schemathesis-report").exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_remote_descriptor_set_extension_is_not_grpc(cli, snapshot_cli):
    assert cli.run("http://127.0.0.1:1/api.pb") == snapshot_cli


@pytest.mark.parametrize(
    ("location", "option"),
    [(None, "--tls-verify=false"), (None, "--proxy=http://127.0.0.1:1"), ("api.binpb", "--url=http://127.0.0.1:1")],
    ids=["tls-verify", "proxy", "descriptor-set-http-url"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unusable_options_refused(ctx, cli, snapshot_cli, location, option):
    server = ctx.grpc.apps.users(tls=True)
    assert cli.run(location or server.target, option) == snapshot_cli


BLOCK_MODULE_AND_RUN = """
import sys
sys.modules[sys.argv[1]] = None
from schemathesis.cli import schemathesis
schemathesis(sys.argv[2:])
"""


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_streaming_method_selected(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    assert cli.run(server.target, "--include-name=pkg.Users/WatchUsers", "--max-examples=1") == snapshot_cli


@pytest.mark.parametrize(
    ("blocked", "command", "source"),
    [
        ("google.protobuf", "run", "reflection"),
        ("google.protobuf", "run", "descriptor-set"),
        ("google.protobuf", "fuzz", "reflection"),
        ("google.protobuf", "fuzz", "descriptor-set"),
        ("grpc", "run", "descriptor-set"),
        ("google.rpc", "run", "reflection"),
    ],
)
def test_missing_grpc_extra(tmp_path, blocked, command, source):
    if source == "reflection":
        args = ["grpc://127.0.0.1:1"]
    else:
        write_descriptor_set(tmp_path, users_descriptor_set())
        args = ["api.binpb", "--url=grpc://127.0.0.1:1"]
    result = subprocess.run(
        [sys.executable, "-c", BLOCK_MODULE_AND_RUN, blocked, command, *args, "--report=junit"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        "Error: Loading gRPC schemas requires the `grpc` extra: `pip install 'schemathesis[grpc]'`\n",
    )
    assert not (tmp_path / "schemathesis-report").exists()


def test_grpc_library_logs_kept_out_of_output(ctx):
    server = ctx.grpc.apps.users()
    tls_target = server.target.replace("grpc://", "grpcs://")
    env = {key: value for key, value in os.environ.items() if key != "GRPC_VERBOSITY"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "schemathesis.cli",
            "run",
            str(server.descriptor_set_path),
            f"--url={tls_target}",
            "--include-name=pkg.Users/GetUser",
            "--phases=fuzzing",
            "--max-examples=1",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert f"TLS handshake with {tls_target.removeprefix('grpcs://')} failed: " in result.stdout
    assert "WRONG_VERSION_NUMBER" in result.stdout
    assert "ssl_transport_security.cc" not in result.stderr


@pytest.mark.parametrize(
    ("tls", "scheme", "source", "extra"),
    [
        (False, "grpcs", "descriptor-set", []),
        (True, "grpc", "descriptor-set", []),
        (True, "grpcs", "descriptor-set", []),
        (True, "grpc", "reflection", []),
        (False, "grpcs", "reflection", ["--wait-for-schema=5"]),
        (True, "grpcs", "reflection", ["--wait-for-schema=5"]),
        (True, "grpc", "reflection", ["--wait-for-schema=5"]),
    ],
    ids=[
        "tls-to-plaintext-server",
        "plaintext-to-tls-server",
        "untrusted-certificate",
        "reflection-plaintext-to-tls-server",
        "reflection-tls-to-plaintext-server-waiting",
        "reflection-untrusted-certificate-waiting",
        "reflection-plaintext-to-tls-server-waiting",
    ],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_tls_failure_reported(ctx, cli, snapshot_cli, tls, scheme, source, extra):
    server = ctx.grpc.apps.users(tls=tls)
    target = f"{scheme}://{server.target.partition('://')[2]}"
    location = [target] if source == "reflection" else ["api.binpb", f"--url={target}"]
    assert (
        cli.run(*location, *extra, "--include-name=pkg.Users/GetUser", "--phases=fuzzing", "--max-examples=1")
        == snapshot_cli
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
def test_rejected_fields_are_replayed_from_cache(ctx, cli, tmp_path, snapshot_cli):
    server = ctx.grpc.apps.users()
    write_descriptor_set(tmp_path, accounts_descriptor_set())
    args = ["api.binpb", f"--url={server.target}", "--max-examples=5", "--seed=1", "--phases=fuzzing"]
    cli.run(*args)
    assert cli.run(*args) == snapshot_cli


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
            "run",
            server.target,
            "--include-name=pkg.Users/GetUser",
            "--max-examples=10",
            "--seed=1",
            "--phases=fuzzing",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unreachable_descriptor_set_target(cli, tmp_path, snapshot_cli):
    write_descriptor_set(tmp_path, users_descriptor_set())
    # A stopped server frees its port, and a server started by another test may take it over.
    with refusing_url() as url:
        target = url.replace("http://", "grpc://")
        assert (
            cli.run("api.binpb", f"--url={target}", "--include-name=pkg.Users/GetUser", "--max-examples=1")
            == snapshot_cli
        )


@pytest.mark.parametrize(
    ("user_id", "extra"),
    [
        (422, []),
        (401, []),
        (13, ["--request-timeout=0.2"]),
        (404, []),
        # A cancelled call says nothing about the data sent, so it is no validation mismatch.
        (499, []),
    ],
    ids=["library-decode-error", "unauthenticated", "deadline-exceeded", "not-found", "cancelled"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_get_user_status(ctx, cli, snapshot_cli, user_id, extra):
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
            "--max-examples=1",
            "--phases=fuzzing",
            *extra,
            hooks=module,
        )
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


@pytest.mark.parametrize("extra", [[], ["--generation-unique-inputs"]], ids=["default", "unique-inputs"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_reaches_created_names_and_page_tokens(ctx, cli, snapshot_cli, extra):
    server = ctx.grpc.apps.library()
    assert (
        cli.run(server.target, "--phases=fuzzing,stateful", "--max-examples=10", "--seed=1", "--warnings=off", *extra)
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "include",
    [
        ["--include-name=library.Library/CreateBook", "--include-name=library.Library/GetBook"],
        ["--include-name=library.Library/ListBooks"],
    ],
    ids=["created-name", "page-token"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_follows_links(ctx, cli, snapshot_cli, include):
    server = ctx.grpc.apps.library()
    # The run ends at the first failure; the time limit only stops a regression from hanging the test.
    assert cli.main("fuzz", server.target, "--max-time=60", *include) == snapshot_cli


def test_unique_inputs_send_a_repeated_message_once(ctx, cli):
    server = ctx.grpc.apps.users()
    # The hook changes every case the same way after the run decides whether it was already sent.
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {"userId": 1}

@schemathesis.hook
def before_call(context, case, kwargs):
    case.body["userId"] = 3
        """
    )
    cli.main(
        "run",
        server.target,
        "--include-name=pkg.Users/GetUser",
        "--max-examples=10",
        "--generation-unique-inputs",
        "--phases=fuzzing",
        hooks=module,
    )
    assert [call.method for call in server.calls] == ["GetUser"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unique_inputs_report_unencodable_messages(ctx, cli, snapshot_cli):
    server = ctx.grpc.apps.users()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def map_body(context, body):
    return {**body, "unknown": 1}
        """
    )
    assert (
        cli.main(
            "run",
            server.target,
            "--include-name=pkg.Users/GetUser",
            "--max-examples=1",
            "--generation-unique-inputs",
            "--phases=fuzzing",
            hooks=module,
        )
        == snapshot_cli
    )
