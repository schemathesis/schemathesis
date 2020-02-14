import platform
import sys
from pathlib import Path

import pytest
from _pytest import pytester

HERE = Path(__file__).absolute().parent

if platform.system() == "Windows" and (sys.version_info.major >= 3 and sys.version_info.minor >= 8):
    AIOHTTP_OUTPUT = "DEBUG:asyncio:Using proactor: IocpProactor"
else:
    AIOHTTP_OUTPUT = "DEBUG:asyncio:Using selector:"


@pytest.mark.parametrize(
    "framework, expected", (("flask", "INFO:werkzeug: * Running on"), ("aiohttp", AIOHTTP_OUTPUT),),
)
def test_app(testdir, aiohttp_unused_port, framework, expected):
    # When the testing app is run from CMD
    port = aiohttp_unused_port()
    with pytest.raises(pytester.Testdir.TimeoutExpired):
        testdir.run(sys.executable, f"{HERE / 'apps/__init__.py'}", str(port), f"--framework={framework}", timeout=2.0)
    with testdir.tmpdir.join("stderr").open() as fd:
        stderr = fd.read()
    # Then it should start OK and emit debug logs
    assert expected in stderr
