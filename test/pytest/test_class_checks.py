import pathlib

import pytest

_ALWAYS_FAIL_CHECK = """
@schemathesis.check
class AlwaysFail:
    def after_run(self, ctx):
        raise AssertionError("always fails in after_run")
"""


@pytest.mark.parametrize(
    "test_body",
    [
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
""",
        """
@schema.parametrize()
def test_api(case):
    pass
""",
        """
@schemathesis.pytest.parametrize(simple=schema)
def test_api(case):
    pass
""",
    ],
    ids=["from_fixture", "schema_parametrize", "pytest_parametrize"],
)
def test_after_run_failure_reported(testdir, restore_checks, test_body):
    testdir.make_test(_ALWAYS_FAIL_CHECK + test_body)
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret != 0
    # CLI-style rendering: a `- <title>` bullet and an indented message.
    result.stdout.fnmatch_lines(["*- Custom check failed: `AlwaysFail`*", "*    always fails in after_run*"])


_SCHEMA_PARAMETRIZE_BODY = """
@schema.parametrize()
def test_api(case):
    pass
"""


def test_after_run_skipped_on_collect_only(testdir, restore_checks):
    testdir.make_test(_ALWAYS_FAIL_CHECK + _SCHEMA_PARAMETRIZE_BODY)
    result = testdir.runpytest("--collect-only")
    # No tests ran, so the whole-run invariant must not be evaluated (and must not fail the session).
    assert result.ret == 0, result.stdout.str()


def test_after_run_failure_reported_without_terminal(testdir, restore_checks):
    testdir.make_test(_ALWAYS_FAIL_CHECK + _SCHEMA_PARAMETRIZE_BODY)
    result = testdir.runpytest("-p", "no:terminal")
    assert result.ret != 0
    # Even without the terminal reporter, the failure text must surface, not be silently dropped.
    assert "always fails in after_run" in (result.stderr.str() + result.stdout.str())


def test_after_run_passes(testdir, restore_checks):
    testdir.make_test(
        """
@schemathesis.check
class AlwaysPass:
    def after_run(self, ctx):
        pass

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == 0


def test_after_run_fires_once_per_test_function(testdir, restore_checks):
    counter_path = str(testdir.tmpdir.join("after_run_count.txt"))
    testdir.make_test(
        f"""
import pathlib
pathlib.Path(r{counter_path!r}).write_text("0")

@schemathesis.check
class Counter:
    def after_run(self, ctx):
        p = pathlib.Path(r{counter_path!r})
        p.write_text(str(int(p.read_text() or 0) + 1))

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_one(case):
    pass

@lazy_schema.parametrize()
def test_two(case):
    pass
"""
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=2)
    assert result.ret == 0
    assert int(pathlib.Path(counter_path).read_text()) == 2


def test_disabled_after_run_check_does_not_execute(testdir, restore_checks):
    testdir.make_test(
        """
@schemathesis.check
class ShouldNotRun:
    def after_run(self, ctx):
        raise AssertionError("should not run")

schema.config.checks.update(excluded_check_names=["ShouldNotRun"])

lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
def test_api(case):
    pass
"""
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
    assert result.ret == 0
