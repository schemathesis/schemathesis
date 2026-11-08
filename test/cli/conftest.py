import pytest

_ENSURE_REACHABILITY = """
@schemathesis.check
class EnsureReachability:
    def __init__(self):
        self.reached = set()
        self.tested = set()

    def after_response(self, ctx, response, case):
        label = case.operation.label
        self.tested.add(label)
        if 200 <= response.status_code < 300:
            self.reached.add(label)

    def after_run(self, ctx):
        unreachable = self.tested - self.reached
        if unreachable:
            raise AssertionError("never returned 2xx: " + ", ".join(sorted(unreachable)))
"""


@pytest.fixture
def ensure_reachability_module(ctx):
    return ctx.write_pymodule(_ENSURE_REACHABILITY)


_UNSORTED_RUN_CHECK_FAILURES = """
from schemathesis.core.failures import CustomFailure, FailureGroup


@schemathesis.check
class ZRunCheck:
    def after_response(self, ctx, response, case):
        pass

    def after_run(self, ctx):
        raise AssertionError("zzz run-level failure")


@schemathesis.check
class ARunCheck:
    def after_response(self, ctx, response, case):
        pass

    def after_run(self, ctx):
        raise FailureGroup(
            [
                CustomFailure(operation=None, title="dup", message="aaa duplicated", exception=AssertionError("dup"))
                for _ in range(2)
            ]
        )
"""


@pytest.fixture
def unsorted_run_check_failures_module(ctx):
    return ctx.write_pymodule(_UNSORTED_RUN_CHECK_FAILURES)


@pytest.fixture
def with_error(ctx):
    return ctx.write_pymodule(
        """
@schemathesis.check
def with_error(ctx, response, case):
    1 / 0
"""
    )


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"
