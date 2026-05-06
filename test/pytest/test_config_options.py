import platform
from textwrap import indent

import pytest


def _schema_setup_from_project_config(update, *, is_lazy):
    config_setup = f"""
config = SchemathesisConfig()
config.projects.default.update({update})
"""
    if is_lazy:
        return f"""
@pytest.fixture
def api_schema():
{indent(config_setup.strip(), "    ")}
    return schemathesis.openapi.from_dict(raw_schema, config=config)

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    return f"""
{config_setup.strip()}
schema = schemathesis.openapi.from_dict(raw_schema, config=config)
"""


def _schema_setup_with_mutation(mutation, *, is_lazy):
    if is_lazy:
        return f"""
@pytest.fixture
def api_schema():
    schema = schemathesis.openapi.from_dict(raw_schema)
{indent(mutation.strip(), "    ")}
    return schema

schema = schemathesis.pytest.from_fixture("api_schema")
"""
    return f"""
schema = schemathesis.openapi.from_dict(raw_schema)
{mutation.strip()}
"""


@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_proxy_is_used(ctx, testdir, is_lazy):
    api = ctx.openapi.apps.success()
    base_url = f"{api.base_url}/api"
    proxy_url = "http://127.0.0.1:8080"
    schema_setup = _schema_setup_from_project_config(
        f'proxy="{proxy_url}", base_url="{base_url}"',
        is_lazy=is_lazy,
    )

    testdir.make_test(
        f"""
{schema_setup}

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_proxy(case):
    with pytest.raises(requests.exceptions.ProxyError):
        case.call()
"""
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_request_timeout_is_used(ctx, testdir, is_lazy):
    api = ctx.openapi.apps.slow()
    base_url = f"{api.base_url}/api"
    timeout = 0.01
    schema_setup = _schema_setup_from_project_config(
        f'request_timeout={timeout}, base_url="{base_url}"',
        is_lazy=is_lazy,
    )

    testdir.make_test(
        f"""
{schema_setup}

@schema.include(path_regex="slow").parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_timeout(case):
    with pytest.raises(requests.exceptions.Timeout):
        case.call()
""",
        paths={"/slow": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_wait_for_schema_is_used(mocker):
    import schemathesis

    # Mock requests.get to track wait_for_schema parameter
    mock_load_from_url = mocker.patch("schemathesis.openapi.loaders.load_from_url")
    mock_response = mocker.Mock()
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.text = '{"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0.0"}, "paths": {}}'
    mock_load_from_url.return_value = mock_response

    wait_for_schema_value = 42.5
    config = schemathesis.config.SchemathesisConfig(wait_for_schema=wait_for_schema_value)

    # Call from_url without explicit wait_for_schema - should use config
    schemathesis.openapi.from_url("http://example.com/openapi.json", config=config)

    # Verify wait_for_schema from config was passed to load_from_url
    assert mock_load_from_url.called
    call_kwargs = mock_load_from_url.call_args[1]
    assert call_kwargs.get("wait_for_schema") == wait_for_schema_value


@pytest.mark.parametrize("is_lazy", [False, True])
def test_fuzzing_phase_max_examples_is_used(testdir, is_lazy):
    max_examples = 3
    schema_setup = _schema_setup_with_mutation(
        f"""
schema.config.phases.fuzzing.generation.update(max_examples={max_examples})
schema.config.phases.examples.enabled = False
schema.config.phases.coverage.enabled = False
schema.config.phases.stateful.enabled = False
""",
        is_lazy=is_lazy,
    )

    testdir.make_test(
        f"""
{schema_setup}

@schema.include(path_regex="test").parametrize()
def test_max_examples(request, case):
    request.config.HYPOTHESIS_CASES += 1
    pass
""",
        paths={
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 0, "maximum": 1000},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([rf"Hypothesis calls: {max_examples}"])


@pytest.mark.skipif(
    platform.system() == "Windows", reason="conn.close() on Windows does not raise ConnectionError on the client side"
)
@pytest.mark.parametrize("is_lazy", [False, True])
def test_config_request_retries_is_used(testdir, is_lazy):
    # Server drops first 2 connections, succeeds on 3rd.
    # Without request_retries=2 the test would raise ConnectionError; with it, it passes.
    schema_setup = _schema_setup_from_project_config(
        'request_retries=2, base_url=f"http://127.0.0.1:{port}"',
        is_lazy=is_lazy,
    )

    testdir.make_test(
        f"""
import socket, threading

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 0))
port = server.getsockname()[1]
server.listen(10)
server.settimeout(5.0)
_attempt = [0]

def _serve():
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            break
        _attempt[0] += 1
        if _attempt[0] <= 2:
            conn.close()  # drop → ConnectionError → retry
        else:
            conn.sendall(
                b"HTTP/1.1 200 OK\\r\\n"
                b"Content-Type: application/json\\r\\n"
                b"Content-Length: 2\\r\\n"
                b"Connection: close\\r\\n"
                b"\\r\\n{{}}"
            )
            conn.close()
            break

_thread = threading.Thread(target=_serve, daemon=True)
_thread.start()

def pytest_unconfigure(config):
    server.close()
    _thread.join(timeout=5.0)

{schema_setup}

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_retries(case):
    case.call()  # succeeds after 2 retries; without retries would raise ConnectionError
""",
    )
    result = testdir.runpytest("-q")
    result.assert_outcomes(passed=1)
