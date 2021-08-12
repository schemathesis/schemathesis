from test.apps.openapi.schema import OpenAPIVersion

import pytest
from _pytest.main import ExitCode

import schemathesis

from ..utils import strip_style_win32


@pytest.fixture(autouse=True)
def reset_hooks():
    yield
    schemathesis.hooks.unregister_all()


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_no_failures(cli, schema_url, service, service_token):
    # When Schemathesis.io is enabled and there are no errors
    result = cli.run(
        schema_url, f"--schemathesis-io-token={service_token}", f"--schemathesis-io-url={service.base_url}"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should receive requests
    assert len(service.server.log) == 5, service.server.log
    # Create job
    service.assert_call(0, "/jobs/", 201)
    for idx, event_type in enumerate(("Initialized", "BeforeExecution", "AfterExecution", "Finished"), 1):
        service.assert_call(idx, "/events/", 201, event_type)
    # And it should be noted in the output
    lines = [strip_style_win32(line) for line in result.stdout.splitlines()]
    # This output contains all temporary lines with a spinner - regular terminals handle `\r` and display everything
    # properly. For this test case, just check one line
    assert "Schemathesis.io: SUCCESS" in lines


@pytest.mark.operations("success")
@pytest.mark.service(data={"detail": "Internal Server Error"}, status=500, method="POST", path="/jobs/")
@pytest.mark.parametrize("show_tracebacks", (True, False))
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_server_error(cli, schema_url, service, service_token, show_tracebacks):
    # When Schemathesis.io is enabled but returns 500 on the first call
    args = [schema_url, f"--schemathesis-io-token={service_token}", f"--schemathesis-io-url={service.base_url}"]
    if show_tracebacks:
        args.append("--show-errors-tracebacks")
    result = cli.run(*args)
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then there is only one request to Schemathesis.io
    assert len(service.server.log) == 1
    service.assert_call(0, "/jobs/", 500)
    # And it should be noted in the output
    lines = [strip_style_win32(line) for line in result.stdout.splitlines()]
    assert "Schemathesis.io: ERROR" in lines
    assert "An error happened during uploading reports to Schemathesis.io" in lines
    if show_tracebacks:
        assert "Traceback (most recent call last):" in lines
    else:
        assert lines[-3].startswith("requests.exceptions.HTTPError: 500 Server Error: INTERNAL SERVER ERROR")


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_error_in_another_handler(testdir, cli, schema_url, service, service_token):
    # When a non-Schemathesis.io handler fails
    module = testdir.make_importable_pyfile(
        hook="""
        import click
        import schemathesis
        from schemathesis.cli.handlers import EventHandler
        from schemathesis.runner import events

        class FailingHandler(EventHandler):

            def handle_event(self, context, event):
                1 / 0

        @schemathesis.hooks.register
        def after_init_cli_run_handlers(
            context,
            handlers,
            execution_context
        ):
            handlers.append(FailingHandler())
        """
    )
    result = cli.main(
        "--pre-run",
        module.purebasename,
        "run",
        schema_url,
        f"--schemathesis-io-token={service_token}",
        f"--schemathesis-io-url={service.base_url}",
    )
    # And all handlers are shutdown forcefully
    # And the run fails
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then the Schemathesis.io handler should still try to finish the started job
    service.assert_call(2, "/finish/", 204)


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_error_in_service_handler(testdir, cli, schema_url, service, service_token, mocker):
    # When a Schemathesis.io handler fails
    mocker.patch("schemathesis.service.worker.serialize_event", side_effect=ValueError("Some internal issue"))
    result = cli.run(
        schema_url, f"--schemathesis-io-token={service_token}", f"--schemathesis-io-url={service.base_url}"
    )
    # And all handlers are shutdown forcefully
    # And the run is still successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then the Schemathesis.io handler should still try to finish the started job
    service.assert_call(1, "/finish/", 204)


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_server_timeout(cli, schema_url, service, service_token, mocker):
    # When Schemathesis.io responds slowly
    mocker.patch("schemathesis.service.WORKER_FINISH_TIMEOUT", 0)
    # And the waiting is more than allowed
    result = cli.run(
        schema_url, f"--schemathesis-io-token={service_token}", f"--schemathesis-io-url={service.base_url}"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then the output indicates timeout
    lines = [strip_style_win32(line) for line in result.stdout.splitlines()]
    assert lines[18].endswith("Schemathesis.io: TIMEOUT")
