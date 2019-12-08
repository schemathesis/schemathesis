import sys
from pathlib import Path

import pytest
from _pytest import pytester

HERE = Path(__file__).absolute().parent


@pytest.mark.parametrize(
    "framework, expected", (("flask", "INFO:werkzeug: * Running on"), ("aiohttp", "DEBUG:asyncio:Using selector:"))
)
def test_app(testdir, aiohttp_unused_port, framework, expected):
    # When the testing app is run from CMD
    port = aiohttp_unused_port()
    with pytest.raises(pytester.Testdir.TimeoutExpired):
        testdir.run(sys.executable, f"{HERE / 'apps/__init__.py'}", str(port), f"--framework={framework}", timeout=0.75)
    with testdir.tmpdir.join("stderr").open() as fd:
        stderr = fd.read()
    # Then it should start OK and emit debug logs
    assert expected in stderr
