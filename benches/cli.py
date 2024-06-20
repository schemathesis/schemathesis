import sys

import pytest

pytest_plugins = ["pytester"]
original = list(sys.modules)


@pytest.mark.benchmark
def test_cli_startup(testdir):
    # Measure the import time because running via subprocess does not give proper benchmark results under codspeed

    import schemathesis.cli

    for key in list(sys.modules):
        # PyO3 modules can't be initialized multiple times
        if key != "rpds" and key not in original:
            del sys.modules[key]
    del schemathesis
