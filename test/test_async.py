from importlib import metadata

import pytest
from packaging import version

from .utils import integer

IS_OLD_PYTEST_ASYNCIO_VERSION = version.parse(metadata.version("pytest_asyncio")) < version.parse("0.11.0")
ASYNCIO_PLUGIN_NAME = "pytest_asyncio" if IS_OLD_PYTEST_ASYNCIO_VERSION else "asyncio"

ALL_PLUGINS = {"aiohttp.pytest_plugin": "", ASYNCIO_PLUGIN_NAME: "@pytest.mark.asyncio", "trio": "@pytest.mark.trio"}


def build_pytest_args(plugin):
    disabled_plugins = set(ALL_PLUGINS) - {plugin}
    args = ["-v"]
    for disabled in disabled_plugins:
        args.extend(("-p", f"no:{disabled}"))
    return args


@pytest.fixture(params=list(ALL_PLUGINS))
def plugin(request):
    return request.param


def test_simple(testdir, plugin):
    # When the wrapped test is a coroutine function and pytest-aiohttp/asyncio plugin is used
    marker = ALL_PLUGINS[plugin]
    testdir.make_test(
        f"""
async def func():
    return 1

{marker}
@schema.parametrize()
async def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "GET"
    assert await func() == 1
    if "{plugin}" == "trio":
        import trio

        await trio.sleep(0)
""",
        pytest_plugins=[plugin],
    )
    args = build_pytest_args(plugin)
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=1)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"test_simple.py::test_\[GET /v1/users\] PASSED", r"Hypothesis calls: 1"])


def test_settings_first(testdir, plugin):
    # When `hypothesis.settings` decorator is applied to a coroutine-based test before `parametrize`
    parameters = {"parameters": [integer(name="id", required=True)]}
    marker = ALL_PLUGINS[plugin]
    testdir.make_test(
        f"""
@schema.parametrize()
{marker}
@settings(max_examples=5)
async def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        pytest_plugins=[plugin],
        paths={"/users": {"get": parameters, "post": parameters}},
    )
    args = build_pytest_args(plugin)
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=2)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"Hypothesis calls: 10$"])


def test_aiohttp_client(testdir):
    # When a wrapped test uses `aiohttp_client` fixture from `aiohttp`
    testdir.make_test(
        """
from aiohttp import web
import yaml

@pytest.fixture()
def app():
    saved_requests = []

    async def schema(request):
        content = yaml.dump(raw_schema)
        return web.Response(body=content)

    async def users(request):
        saved_requests.append(request)
        return web.Response()

    app = web.Application()
    app.add_routes([web.get("/schema.yaml", schema), web.get("/v1/users", users)])
    app["saved_requests"] = saved_requests
    return app

@schema.parametrize()
@settings(max_examples=5, suppress_health_check=list(HealthCheck))
async def test_(request, aiohttp_client, app, case):
    request.config.HYPOTHESIS_CASES += 1
    client = await aiohttp_client(app)
    response = await client.request(case.method, f"/v1{case.formatted_path}", headers=case.headers)
    assert response.status < 500
    assert len(app["saved_requests"]) == 1
    assert app["saved_requests"][0].method == "GET"
    assert app["saved_requests"][0].path == "/v1/users"
"""
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])
