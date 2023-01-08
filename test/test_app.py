import os
import platform
import sys
from pathlib import Path

import pytest

from schemathesis.constants import IS_PYTEST_ABOVE_7

if IS_PYTEST_ABOVE_7:
    from _pytest.pytester import Pytester

    TimeoutExpired = Pytester.TimeoutExpired
else:
    from _pytest import pytester

    TimeoutExpired = pytester.Testdir.TimeoutExpired

HERE = Path(__file__).absolute().parent

if platform.system() == "Windows" and (sys.version_info.major >= 3 and sys.version_info.minor >= 8):
    AIOHTTP_OUTPUT = "DEBUG:asyncio:Using proactor: IocpProactor"
else:
    AIOHTTP_OUTPUT = "DEBUG:asyncio:Using selector:"


@pytest.mark.parametrize(
    "framework, expected",
    (
        ("flask", "INFO:werkzeug:"),
        ("aiohttp", AIOHTTP_OUTPUT),
    ),
)
def test_app(testdir, aiohttp_unused_port, framework, expected):
    # When the testing app is run from CMD
    port = aiohttp_unused_port()
    if platform.system() == "Windows":
        timeout = 4.0
    else:
        timeout = 2.0
    if os.getenv("COVERAGE_RUN") == "true":
        timeout *= 2
    with pytest.raises(TimeoutExpired):
        testdir.run(
            sys.executable, str(HERE / "apps/__init__.py"), str(port), f"--framework={framework}", timeout=timeout
        )
    with testdir.tmpdir.join("stderr").open() as fd:
        stderr = fd.read()
    # Then it should start OK and emit debug logs
    assert expected in stderr
