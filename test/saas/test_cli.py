from test.apps.openapi.schema import OpenAPIVersion

import pytest
from _pytest.main import ExitCode


@pytest.mark.operations("success")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_no_failures(cli, schema_url, saas, saas_token):
    # When SaaS is enabled and there are no errors
    result = cli.run(schema_url, f"--saas-token={saas_token}", f"--saas-url={saas.base_url}")
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then it should receive requests
    assert len(saas.server.log) == 5
    # Create job
    saas.assert_call(0, "/jobs/", 201)
    for idx, event_type in enumerate(("Initialized", "BeforeExecution", "AfterExecution", "Finished"), 1):
        saas.assert_call(idx, "/events/", 201, event_type)
    # And it should be noted in the output
    lines = result.stdout.split("\n")
    # This output contains all temporary lines with a spinner - regular terminals handle `\r` and display everything
    # properly. For this test case, just check the line ending
    assert lines[18].endswith("SaaS status: SUCCESS")


@pytest.mark.operations("success")
@pytest.mark.saas(data={"detail": "Internal Server Error"}, status=500, method="POST", path="/jobs/")
@pytest.mark.parametrize("openapi_version", (OpenAPIVersion("3.0"),))
def test_server_error(cli, schema_url, saas, saas_token):
    # When SaaS is enabled but returns 500 on the first call
    result = cli.run(schema_url, f"--saas-token={saas_token}", f"--saas-url={saas.base_url}")
    assert result.exit_code == ExitCode.OK, result.stdout
    # Then there is only one request to SaaS
    assert len(saas.server.log) == 1
    saas.assert_call(0, "/jobs/", 500)
    # And it should be noted in the output
    lines = result.stdout.split("\n")
    assert lines[18].endswith("SaaS status: ERROR")
