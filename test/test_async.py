import pytest

from .utils import integer


@pytest.fixture(params=["aiohttp.pytest_plugin", "pytest_asyncio"])
def plugin(request):
    return request.param


def test_simple(testdir, plugin):
    # When the wrapped test is a coroutine function and pytest-aiohttp/asyncio plugin is used
    testdir.make_test(
        f"""
async def func():
    return 1

@schema.parametrize()
{"@pytest.mark.asyncio" if plugin == "pytest_asyncio" else ""}
async def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method == "GET"
    assert await func() == 1
""",
        pytest_plugins=[plugin],
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"test_simple.py::test_\[GET /v1/users\] PASSED", r"Hypothesis calls: 1"])


def test_settings_first(testdir, plugin):
    # When `hypothesis.settings` decorator is applied to a coroutine-based test before `parametrize`
    parameters = {"parameters": [integer(name="id", required=True)]}
    testdir.make_test(
        f"""
@schema.parametrize()
{"@pytest.mark.asyncio" if plugin == "pytest_asyncio" else ""}
@settings(max_examples=5)
async def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.full_path == "/v1/users"
    assert case.method in ("GET", "POST")
""",
        pytest_plugins=[plugin],
        paths={"/users": {"get": parameters, "post": parameters}},
    )
    result = testdir.runpytest("-v", "-s")
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
@settings(max_examples=5, suppress_health_check=HealthCheck.all())
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
