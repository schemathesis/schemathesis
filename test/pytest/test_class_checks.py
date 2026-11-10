import pytest

_ALWAYS_FAIL_CHECK = """
@schemathesis.check
class AlwaysFail:
    def after_run(self, ctx):
        raise AssertionError("always fails in after_run")
"""

_SCHEMA_PARAMETRIZE_BODY = """
@schema.parametrize()
def test_api(case):
    pass
"""


@pytest.mark.parametrize(
    ("test_body", "args", "passed"),
    [
        (
            """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
""",
            (),
            1,
        ),
        (_SCHEMA_PARAMETRIZE_BODY, (), 1),
        (
            """
@schemathesis.pytest.parametrize(simple=schema)
def test_api(case):
    pass
""",
            (),
            1,
        ),
        # One test per worker: both workers fail the check identically, and the failure is shown once.
        (
            _SCHEMA_PARAMETRIZE_BODY
            + """
@schema.parametrize()
def test_api_again(case):
    pass
""",
            ("-n", "2"),
            2,
        ),
    ],
    ids=["from_fixture", "schema_parametrize", "pytest_parametrize", "xdist"],
)
def test_after_run_failure_reported(testdir, test_body, args, passed):
    testdir.make_test(_ALWAYS_FAIL_CHECK + test_body)
    result = testdir.runpytest(*args)
    result.assert_outcomes(passed=passed)
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*- ST901 Custom check failed: `AlwaysFail`*", "*    always fails in after_run*"])
    assert result.stdout.str().count("ST901") == 1


def test_after_run_skipped_on_collect_only(testdir):
    testdir.make_test(_ALWAYS_FAIL_CHECK + _SCHEMA_PARAMETRIZE_BODY)
    result = testdir.runpytest("--collect-only")
    # No tests ran, so after_run checks must not run and must not fail the session.
    assert result.ret == 0, result.stdout.str()


_SEEN_OPERATIONS_CHECK = """
@schemathesis.check
class SeenOperations:
    def __init__(self):
        self.seen = set()

    def after_response(self, ctx, response, case):
        self.seen.add(case.operation.label)

    def after_run(self, ctx):
        if {raise_when_seen} and self.seen:
            raise AssertionError("seen: " + ", ".join(sorted(self.seen)))
"""


@pytest.mark.parametrize(
    ("raise_when_seen", "exit_code", "expected_lines"),
    [
        (True, 1, ["*- ST901 Custom check failed: `SeenOperations`*", "*    seen: *POST /users/*"]),
        (False, 0, ["*= 1 passed in *"]),
    ],
    ids=["failing", "passing"],
)
def test_after_run_for_state_machine(ctx, testdir, raise_when_seen, exit_code, expected_lines):
    api = ctx.openapi.apps.users_crud()
    testdir.make_test(
        _SEEN_OPERATIONS_CHECK.format(raise_when_seen=raise_when_seen)
        + f"""
schema = schemathesis.openapi.from_url("{api.schema_url}")
schema.config.update(base_url="{api.base_url}")
schema.config.checks.update(included_check_names=["SeenOperations"])
TestCase = schema.as_state_machine().TestCase
TestCase.settings = settings(max_examples=2, stateful_step_count=2, deadline=None, derandomize=True)
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == exit_code
    result.stdout.fnmatch_lines(expected_lines)


def test_after_run_failure_reported_without_terminal(testdir):
    testdir.make_test(_ALWAYS_FAIL_CHECK + _SCHEMA_PARAMETRIZE_BODY)
    result = testdir.runpytest("-p", "no:terminal")
    assert result.ret != 0
    # Failure text must still surface without the terminal reporter, not be silently dropped.
    assert "always fails in after_run" in (result.stderr.str() + result.stdout.str())


# One marker file per call: xdist workers finish concurrently.
@pytest.mark.parametrize("args", [(), ("-n", "2")], ids=["in-process", "xdist"])
def test_after_run_fires_once_per_test_function(testdir, args):
    calls_dir = testdir.mkdir("after_run_calls")
    testdir.make_test(
        f"""
import pathlib
import uuid

@schemathesis.check
class Counter:
    def after_run(self, ctx):
        (pathlib.Path(r{str(calls_dir)!r}) / uuid.uuid4().hex).touch()

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_one(case):
    pass

@lazy_schema.parametrize()
def test_two(case):
    pass
"""
    )
    result = testdir.runpytest("-v", *args)
    result.assert_outcomes(passed=2)
    assert result.ret == 0
    assert "Run checks" not in result.stdout.str()
    assert len(calls_dir.listdir()) == 2


# With `-n 2`, the worker that gets no test still collects both schemas.
@pytest.mark.parametrize("args", [(), ("-n", "2")], ids=["in-process", "xdist"])
def test_after_run_skips_schemas_without_executed_tests(testdir, ctx, args):
    api = ctx.openapi.apps.success()
    testdir.make_test(
        f"""
@schemathesis.check
class SawResponses:
    def __init__(self):
        self.responses = 0

    def after_response(self, ctx, response, case):
        self.responses += 1

    def after_run(self, ctx):
        assert self.responses, "no responses seen"

schema.config.update(base_url="{api.base_url}")
deselected_schema = schemathesis.openapi.from_dict(raw_schema)

@schema.parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate()

@deselected_schema.parametrize()
def test_deselected(case):
    pass
""",
        schema=api.spec,
    )
    result = testdir.runpytest("-k", "test_api", *args)
    result.assert_outcomes(passed=1)
    assert result.ret == 0
    assert "Run checks" not in result.stdout.str()


def test_after_run_failure_alongside_failed_test(testdir):
    testdir.make_test(
        _ALWAYS_FAIL_CHECK
        + """
@schema.parametrize()
def test_api(case):
    raise AssertionError("test body fails")
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(failed=1)
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*- ST901 Custom check failed: `AlwaysFail`*", "*    always fails in after_run*"])
