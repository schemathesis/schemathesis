import sys
from pathlib import Path

import pytest
from _pytest import pytester

HERE = Path(__file__).absolute().parent


def test_app(testdir, aiohttp_unused_port):
    # When the testing app is run from CMD
    port = aiohttp_unused_port()
    with pytest.raises(pytester.Testdir.TimeoutExpired):
        testdir.run(sys.executable, f"{HERE / 'app/__init__.py'}", str(port), timeout=0.75)
    with testdir.tmpdir.join("stderr").open() as fd:
        stderr = fd.read()
    # Then it should start OK and emit debug logs
    assert "DEBUG:asyncio:Using selector:" in stderr
