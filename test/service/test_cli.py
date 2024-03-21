import json
import re
from queue import Queue

import pytest
from _pytest.main import ExitCode
from requests import Timeout

from schemathesis.cli.output.default import SERVICE_ERROR_MESSAGE, wait_for_report_handler
from schemathesis.constants import USER_AGENT
from schemathesis.service import ci, events
from schemathesis.service.constants import (
    CI_PROVIDER_HEADER,
    REPORT_CORRELATION_ID_HEADER,
    REPORT_ENV_VAR,
    UPLOAD_SOURCE_HEADER,
)
from schemathesis.service.hosts import load_for_host

from ..utils import strip_style_win32


def get_stdout_lines(stdout):
    return [strip_style_win32(line) for line in stdout.splitlines()]


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_no_failures(cli, schema_url, service, next_url, upload_message):
    # When Schemathesis.io is enabled and there are no errors
    result = cli.run(
        "my-api",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
        "--report",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should receive requests
    assert len(service.server.log) == 3, service.server.log
    # And all requests should have the proper User-Agent
    for request, _ in service.server.log:
        assert request.headers["User-Agent"] == USER_AGENT
    service.assert_call(0, "/cli/projects/my-api/", 200)
    service.assert_call(1, "/cli/analysis/", 200)
    service.assert_call(2, "/reports/upload/", 202)
    # And it should be noted in the output
    lines = get_stdout_lines(result.stdout)
    # This output contains all temporary lines with a spinner - regular terminals handle `\r` and display everything
    # properly. For this test case, just check one line
    assert "Upload: COMPLETED" in lines
    assert upload_message in lines
    assert next_url in lines


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_github_suggestion(monkeypatch, cli, schema_url, snapshot_cli):
    monkeypatch.setenv(ci.GitHubActionsEnvironment.variable_name, "true")
    assert cli.run(schema_url) == snapshot_cli


@pytest.mark.operations("success")
@pytest.mark.service(data={"detail": "Internal Server Error"}, status=500, method="POST", path="/reports/upload/")
@pytest.mark.openapi_version("3.0")
def test_server_error(cli, schema_url, service):
    # When Schemathesis.io is enabled but returns 500 on the first call
    args = [
        schema_url,
        "my-api",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
        "--report",
    ]
    result = cli.run(*args)
    assert result.exit_code == ExitCode.OK, result.stdout
    assert len(service.server.log) == 2
    service.assert_call(1, "/reports/upload/", 500)
    # And it should be noted in the output
    lines = get_stdout_lines(result.stdout)
    assert "Upload: ERROR" in lines
    assert "Please, try again in 30 minutes" in lines


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_error_in_another_handler(testdir, cli, schema_url, service, snapshot_cli):
    # When a non-Schemathesis.io handler fails
    module = testdir.make_importable_pyfile(
        hook="""
        import click
        import schemathesis
        from schemathesis.cli.handlers import EventHandler
        from schemathesis.runner import events

        class FailingHandler(EventHandler):

            def handle_event(self, context, event):
                raise ZeroDivisionError

        @schemathesis.hook
        def after_init_cli_run_handlers(
            context,
            handlers,
            execution_context
        ):
            handlers.append(FailingHandler())
        """
    )
    # And all handlers are shutdown forcefully
    # And the run fails
    assert (
        cli.main(
            "run",
            schema_url,
            "my-api",
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_server_timeout(cli, schema_url, service, mocker):
    # When Schemathesis.io responds slowly
    mocker.patch("schemathesis.cli.output.default.wait_for_report_handler", return_value=events.Timeout())
    # And the waiting is more than allowed
    result = cli.run(
        schema_url,
        "my-api",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
        "--report",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = get_stdout_lines(result.stdout)
    # And meta information should be displayed
    assert lines[29] in ("Compressed report size: 1 KB", "Compressed report size: 2 KB")
    assert lines[30] == f"Uploading reports to {service.base_url} ..."
    # Then the output indicates timeout
    assert lines[31] == "Upload: TIMEOUT"


def test_wait_for_report_handler():
    assert wait_for_report_handler(Queue(), "", 0.0) == events.Timeout()


@pytest.mark.service(
    data={"title": "Unauthorized", "status": 401, "detail": "Could not validate credentials"},
    status=401,
    method="GET",
    path=re.compile("/cli/projects/.*/"),
)
@pytest.mark.openapi_version("3.0")
def test_unauthorized(cli, schema_url, service, snapshot_cli):
    # When the token is invalid
    # Then a proper error message should be displayed
    assert (
        cli.run("my-api", "--schemathesis-io-token=invalid", f"--schemathesis-io-url={service.base_url}")
        == snapshot_cli
    )


@pytest.mark.service(
    data={"title": "Bad request", "status": 400, "detail": "Please, upgrade your CLI"},
    status=400,
    method="POST",
    path="/reports/upload/",
)
@pytest.mark.openapi_version("3.0")
def test_client_error_on_upload(cli, schema_url, service, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "my-api",
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
            "--report",
        )
        == snapshot_cli
    )


@pytest.mark.service(
    data="Content-Type error",
    status=400,
    method="POST",
    path="/reports/upload/",
)
@pytest.mark.openapi_version("3.0")
def test_unknown_error_on_upload(cli, schema_url, service, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            "my-api",
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
            "--report",
        )
        == snapshot_cli
    )


@pytest.mark.service(
    data={"title": "Bad request", "status": 400, "detail": "Please, upgrade your CLI"},
    status=400,
    method="GET",
    path="/cli/projects/my-api/",
)
@pytest.mark.openapi_version("3.0")
def test_client_error_on_project_details(cli, schema_url, service, snapshot_cli):
    assert (
        cli.run(
            "my-api",
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
            "--report",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_connection_issue(cli, schema_url, service, mocker):
    # When there is a connection issue
    mocker.patch("schemathesis.service.report.serialize_event", side_effect=Timeout)
    result = cli.run(
        schema_url,
        "my-api",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
        "--report",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then a proper error message should be displayed
    lines = get_stdout_lines(result.stdout)
    assert f"{SERVICE_ERROR_MESSAGE}:" in lines
    assert "Please, consider" not in result.stdout
    assert "Timeout" in result.stdout


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_anonymous_upload_with_name(cli, schema_url, hosts_file, service, upload_message, next_url):
    # When there is API name
    # And there is no token
    result = cli.run(
        schema_url, "my-api", "--report", f"--hosts-file={hosts_file}", f"--schemathesis-io-url={service.base_url}"
    )
    # Then the report should be uploaded
    assert result.exit_code == ExitCode.OK, result.stdout
    lines = get_stdout_lines(result.stdout)
    assert "Upload: COMPLETED" in lines
    assert upload_message in lines
    assert next_url in lines


@pytest.mark.openapi_version("3.0")
def test_api_name(cli, schema_url, service, next_url):
    # When API name does not exist
    result = cli.run(
        schema_url,
        "my-api",
        "--report",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then the report should be uploaded anyway
    assert next_url in result.stdout.strip()


@pytest.mark.service(
    data={"title": "Not found", "status": 404, "detail": "Project not found"},
    status=404,
    method="GET",
    path=re.compile("/cli/projects/.*/"),
)
@pytest.mark.openapi_version("3.0")
def test_invalid_name(cli, schema_url, service, next_url):
    # When API name does not exist
    # And API data is loaded by name
    result = cli.run(
        "my-api",
        "--report",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then the report should be uploaded anyway
    assert result.stdout.strip() == "❌ API with name `my-api` not found!"


@pytest.mark.service(
    data={"title": "Forbidden", "status": 403, "detail": "FORBIDDEN!"},
    status=403,
    method="GET",
    path=re.compile("/cli/projects/.*/"),
)
@pytest.mark.openapi_version("3.0")
def test_forbidden(cli, schema_url, service):
    # When there is 403 from Schemathesis.io
    result = cli.run("my-api", f"--schemathesis-io-token={service.token}", f"--schemathesis-io-url={service.base_url}")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then the error should be immediately visible
    assert result.stdout.strip() == "❌ FORBIDDEN!"


def test_not_authenticated_with_name(cli, snapshot_cli):
    # When the user is not authenticated
    # And uses an API name
    # Then the error message should note it
    assert cli.run("my-api") == snapshot_cli


def test_two_names(cli, service, snapshot_cli):
    # When the user passes api name twice
    # Then the error message should note it
    assert (
        cli.run(
            "my-api", "my-api", f"--schemathesis-io-token={service.token}", f"--schemathesis-io-url={service.base_url}"
        )
        == snapshot_cli
    )


@pytest.mark.operations("success")
def test_authenticated_with_name(cli, service):
    # When the user is authenticated
    # And uses an API name
    result = cli.run("my-api", f"--schemathesis-io-token={service.token}", f"--schemathesis-io-url={service.base_url}")
    # Then the schema location should be loaded
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "1 passed" in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_permission_denied_on_hosts_creation(mocker, cli, schema_url, service, hosts_file):
    # When the hosts file can't be created
    mocker.patch("pathlib.Path.mkdir", side_effect=PermissionError("Permission Denied"))
    # Then it should not make the run fail
    result = cli.run(schema_url, f"--hosts-file={hosts_file}")
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.mark.operations("success")
@pytest.mark.service(data={"username": "TestUser"}, status=200, method="POST", path="/auth/cli/login/")
@pytest.mark.openapi_version("3.0")
def test_anonymous_upload(cli, schema_url, service, hosts_file, correlation_id):
    # When upload is anonymous
    result = cli.run(schema_url, f"--schemathesis-io-url={service.base_url}", f"--hosts-file={hosts_file}", "--report")
    # Then it is successful
    assert result.exit_code == ExitCode.OK, result.stdout
    assert SERVICE_ERROR_MESSAGE not in result.stdout
    service.assert_call(1, "/reports/upload/", 202)
    # And the returned correlation id should be properly stored
    assert load_for_host(service.hostname, hosts_file)["correlation_id"] == correlation_id
    # And the same correlation id is used for the next upload
    cli.run(schema_url, f"--schemathesis-io-url={service.base_url}", f"--hosts-file={hosts_file}", "--report")
    assert service.server.log[3][0].headers[REPORT_CORRELATION_ID_HEADER] == correlation_id
    # And later auth should not override existing correlation_id
    result = cli.auth.login(
        "sample_token", f"--hosts-file={hosts_file}", f"--hostname={service.hostname}", "--protocol=http"
    )
    # Then the command succeeds
    assert result.exit_code == 0, result.stdout
    assert load_for_host(service.hostname, hosts_file)["correlation_id"] == correlation_id


@pytest.mark.parametrize("name", (None, "test-api"))
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_save_to_file(cli, schema_url, tmp_path, read_report, service, name):
    # When an argument is provided to the `--report` option
    report_file = tmp_path / "report.tar.gz"
    if name is not None:
        args = (name,)
    else:
        args = ()
    result = cli.run(schema_url, *args, f"--report={report_file}")
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then the report should be saved to a file
    payload = report_file.read_bytes()
    with read_report(payload) as tar:
        assert len(tar.getmembers()) == 10
        metadata = json.load(tar.extractfile("metadata.json"))
        assert metadata["ci"] is None
        assert metadata["api_name"] == name
    # And it should be written in CLI
    assert f"Report is saved to {report_file}" in result.stdout
    # And should not be sent to the SaaS
    assert not service.server.log


@pytest.mark.parametrize("kind", ("service", "file"))
@pytest.mark.parametrize("telemetry", ("true", "false"))
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_report_via_env_var(cli, schema_url, tmp_path, read_report, service, monkeypatch, kind, telemetry):
    # When report processing is triggered via an env var
    if kind == "service":
        env_variable_value = "true"
    else:
        report_file = tmp_path / "report.tar.gz"
        env_variable_value = str(report_file)
    monkeypatch.setenv(REPORT_ENV_VAR, env_variable_value)
    result = cli.run(
        schema_url, f"--schemathesis-io-url={service.base_url}", f"--schemathesis-io-telemetry={telemetry}"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then the report should be processed according to the env var value
    if kind == "service":
        assert service.server.log
        payload = service.server.log[1][0].data
    else:
        payload = report_file.read_bytes()
        # And should not be sent to the SaaS
        assert not service.server.log
        # And it should be written in CLI
        assert f"Report is saved to {report_file}" in result.stdout
        assert not service.server.log
    with read_report(payload) as tar:
        assert len(tar.getmembers()) == 10
        metadata = json.load(tar.extractfile("metadata.json"))
        assert metadata["ci"] is None
        if telemetry == "true":
            assert metadata["usage"]
        else:
            assert not metadata["usage"]


DEFAULT_GITHUB_ENVIRONMENT = ci.GitHubActionsEnvironment(
    api_url="https://api.github.com",
    repository="schemathesis/schemathesis",
    actor="Stranger6667",
    sha="e56e13224f08469841e106449f6467b769e2afca",
    run_id="1658821493",
    workflow="Build job",
    head_ref="dd/report-ci",
    base_ref="main",
    ref=None,
    action_ref=None,
)


@pytest.mark.parametrize(
    "environment",
    (
        (
            DEFAULT_GITHUB_ENVIRONMENT,
            ci.GitLabCIEnvironment(
                api_v4_url="https://gitlab.com/api/v4",
                project_id="7",
                user_login="Stranger6667",
                commit_sha="e56e13224f08469841e106449f6467b769e2afca",
                commit_branch=None,
                merge_request_source_branch_name="dd/report-ci",
                merge_request_target_branch_name="main",
                merge_request_iid="43",
            ),
        )
    ),
)
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_ci_environment(monkeypatch, cli, schema_url, tmp_path, read_report, service, environment):
    # When executed in CI
    monkeypatch.setenv(environment.variable_name, "true")
    for key, value in environment.as_env().items():
        if value is not None:
            monkeypatch.setenv(key, value)
    report_file = tmp_path / "report.tar.gz"
    result = cli.run(schema_url, f"--report={report_file}")
    assert result.exit_code == ExitCode.OK, result.stdout
    # And CI information is displayed in stdout
    lines = get_stdout_lines(result.stdout)
    assert lines[20] == f"{environment.verbose_name} detected:"
    key, value = next(iter(environment.as_env().items()))
    assert lines[21] == f"  -> {key}: {value}"
    # And missing env vars are not displayed
    key, _ = next(filter(lambda kv: kv[1] is None, iter(environment.as_env().items())))
    assert key not in result.stdout
    # Then CI variables should be stored inside metadata
    payload = report_file.read_bytes()
    with read_report(payload) as tar:
        assert json.load(tar.extractfile("metadata.json"))["ci"] == environment.asdict()


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_send_provider_header(monkeypatch, cli, schema_url, service):
    # When executed in CI
    monkeypatch.setenv(DEFAULT_GITHUB_ENVIRONMENT.variable_name, "true")
    for key, value in DEFAULT_GITHUB_ENVIRONMENT.as_env().items():
        if value is not None:
            monkeypatch.setenv(key, value)
    result = cli.run(
        schema_url, "--report", f"--schemathesis-io-token={service.token}", f"--schemathesis-io-url={service.base_url}"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then send CI provider name in a header
    assert service.server.log[1][0].headers[CI_PROVIDER_HEADER] == "github"


PAYLOAD_TOO_LARGE_MESSAGE = "Your report is too large. The limit is 100 KB, but your report is 101 KB."
PAYLOAD_TOO_LARGE = {
    "data": {"title": "Payload Too Large", "status": 413, "detail": PAYLOAD_TOO_LARGE_MESSAGE},
    "status": 413,
    "method": "POST",
    "path": "/reports/upload/",
}


@pytest.mark.service(**PAYLOAD_TOO_LARGE)
@pytest.mark.openapi_version("3.0")
def test_too_large_payload(cli, schema_url, service):
    # When the report exceeds the size limit
    result = cli.run(
        schema_url,
        "my-api",
        "--report",
        f"--schemathesis-io-token={service.token}",
        f"--schemathesis-io-url={service.base_url}",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then it should be correctly handled & reported in CLI
    lines = get_stdout_lines(result.stdout)
    assert "Upload: FAILED" in lines
    assert PAYLOAD_TOO_LARGE_MESSAGE in lines


@pytest.fixture
def report_file(tmp_path, cli, schema_url):
    report_file = tmp_path / "report.tar.gz"
    result = cli.run(schema_url, f"--report={report_file}", "--show-trace")
    assert result.exit_code == ExitCode.OK, result.stdout
    return report_file


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_upload_success(cli, snapshot_cli, service, report_file):
    assert (
        cli.main(
            "upload",
            str(report_file),
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
        )
        == snapshot_cli
    )
    assert service.server.log[0][0].headers[UPLOAD_SOURCE_HEADER] == "upload_command"


@pytest.mark.service(**PAYLOAD_TOO_LARGE)
@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_upload_failure(cli, snapshot_cli, service, report_file):
    assert (
        cli.main(
            "upload",
            str(report_file),
            f"--schemathesis-io-token={service.token}",
            f"--schemathesis-io-url={service.base_url}",
        )
        == snapshot_cli
    )
