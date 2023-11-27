import os
import platform
import sys
from pathlib import Path

import pytest

from schemathesis._dependency_versions import IS_PYTEST_ABOVE_7

if IS_PYTEST_ABOVE_7:
    from _pytest.pytester import Pytester

    TimeoutExpired = Pytester.TimeoutExpired
else:
    from _pytest import pytester

    TimeoutExpired = pytester.Testdir.TimeoutExpired

HERE = Path(__file__).absolute().parent


@pytest.mark.parametrize(
    "framework",
    ("flask", "aiohttp"),
)
def test_app(testdir, aiohttp_unused_port, framework):
    # When the testing app is run from CMD
    port = aiohttp_unused_port()
    if platform.system() == "Windows":
        timeout = 5.0
    else:
        timeout = 3.0
    if os.getenv("COVERAGE_RUN") == "true":
        timeout *= 2
    with pytest.raises(TimeoutExpired):
        testdir.run(
            sys.executable, str(HERE / "apps/__init__.py"), str(port), f"--framework={framework}", timeout=timeout
        )
    with testdir.tmpdir.join("stdout").open() as fd:
        stdout = fd.read()
    # Then it should start OK and emit debug logs
    assert "Schemathesis test server is running!" in stdout
