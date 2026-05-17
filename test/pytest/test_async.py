import pytest

from schemathesis.generation.modes import GenerationMode

from ..utils import integer

ALL_PLUGINS = {"asyncio": "@pytest.mark.asyncio", "trio": "@pytest.mark.trio"}


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
    # When the wrapped test is a coroutine function and the asyncio/trio plugin is used
    marker = ALL_PLUGINS[plugin]
    testdir.make_test(
        f"""
async def func():
    return 1

{marker}
@schema.parametrize()
async def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.method == "GET"
    assert await func() == 1
    if "{plugin}" == "trio":
        import trio

        await trio.sleep(0)
""",
        pytest_plugins=[plugin],
        generation_modes=[GenerationMode.POSITIVE],
    )
    args = build_pytest_args(plugin)
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=1)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"test_simple.py::test_\[GET /users\] PASSED", r"Hypothesis calls: 2"])


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
    assert case.method in ("GET", "POST")
""",
        pytest_plugins=[plugin],
        paths={"/users": {"get": parameters, "post": parameters}},
        generation_modes=[GenerationMode.POSITIVE],
    )
    args = build_pytest_args(plugin)
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=2)
    # Then it should be executed as any other test
    result.stdout.re_match_lines([r"Hypothesis calls: 12$"])
