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
def ensure_reachability_module(ctx, restore_checks):
    yield ctx.write_pymodule(_ENSURE_REACHABILITY)
