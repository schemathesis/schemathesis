import sys

import pytest

pytest_plugins = ["pytester"]
original = list(sys.modules)


def _purge():
    before = dict(sys.modules)
    # PyO3 modules can't be initialized multiple times
    purged = {key for key in before if key != "rpds" and key not in original}
    # Submodules of surviving packages: `from package import module` skips the import while the attribute is set
    attributes = []
    for key in purged:
        parent, _, name = key.rpartition(".")
        if parent in before and parent not in purged and before[key] is not None:
            attributes.append((before[parent], name, before[key]))
    for key in purged:
        del sys.modules[key]
    for package, name, _ in attributes:
        package.__dict__.pop(name, None)
    return (before, attributes), {}


def _import_cli(before, attributes):
    import schemathesis.cli  # noqa: F401


def _restore(before, attributes):
    # Later benchmarks must keep using the modules they imported
    for key in list(sys.modules):
        if key not in before:
            del sys.modules[key]
    sys.modules.update(before)
    for package, name, module in attributes:
        setattr(package, name, module)


@pytest.mark.benchmark(group="cli-startup")
def test_cli_startup(benchmark, testdir):
    # Measure the import time because running via subprocess does not give proper benchmark results under codspeed
    benchmark.pedantic(_import_cli, setup=_purge, teardown=_restore)
