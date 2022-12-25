import pytest
import yaml

import schemathesis

# See GH-999
# Tests for behavior when the API schema contains operations that are valid but contains an unresolvable reference
# Note that these errors can't be detected with meta-schema validation


@pytest.fixture
def schema(open_api_3_schema_with_recoverable_errors):
    return schemathesis.from_dict(open_api_3_schema_with_recoverable_errors)


EXPECTED_OUTPUT_LINES = [
    # Path-level error. no method is displayed
    r".*test_\[/foo\] FAILED",
    # Valid operation
    r".*test_\[GET /bar\] PASSED",
    # Operation-level error
    r".*test_\[POST /bar\] FAILED",
    # The error in both failing cases
    ".*Unresolvable JSON pointer:.*",
]


def test_in_pytest(testdir, open_api_3_schema_with_recoverable_errors):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
    """,
        schema=open_api_3_schema_with_recoverable_errors,
    )
    result = testdir.runpytest("-v")
    # Then valid operation should be tested
    # And errors on the single operation error should be displayed
    result.assert_outcomes(passed=1, failed=2)
    result.stdout.re_match_lines(EXPECTED_OUTPUT_LINES)


def test_in_pytest_subtests(testdir, is_older_subtests, open_api_3_schema_with_recoverable_errors):
    testdir.make_test(
        """
lazy_schema = schemathesis.from_pytest_fixture("simple_schema")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
    """,
        schema=open_api_3_schema_with_recoverable_errors,
    )
    result = testdir.runpytest("-v", "-s")
    # Then valid operation should be tested
    # And errors on the single operation error should be displayed
    result.assert_outcomes(passed=1, failed=2)
    if is_older_subtests:
        expected = EXPECTED_OUTPUT_LINES
    else:
        expected = [
            # Path-level error. no method is displayed
            r".*test_\[/foo\] SUBFAIL",
            # Valid operation
            r".*test_\[GET /bar\] SUBPASS",
            # Operation-level error
            r".*test_\[POST /bar\] SUBFAIL",
            # The error in both failing cases
            ".*Unresolvable JSON pointer:.*",
        ]
    result.stdout.re_match_lines(expected)


@pytest.mark.parametrize("workers", (1, 2))
def test_in_cli(testdir, cli, open_api_3_schema_with_recoverable_errors, workers):
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(open_api_3_schema_with_recoverable_errors))
    result = cli.run(str(schema_file), "--dry-run", "--show-errors-tracebacks", f"--workers={workers}")
    lines = result.stdout.splitlines()
    # Then valid operation should be tested
    # And errors on the single operation error should be displayed
    if workers == 1:
        assert lines[7].startswith("GET /bar .")
        assert lines[8].startswith("POST /bar E")
    else:
        assert lines[7] in ("E.", ".E")
    error = "Unresolvable JSON pointer: 'components/UnknownParameter'"
    assert len([line for line in lines if error in line]) == 1
    assert "1 passed, 2 errored" in lines[-1]
    assert "____ /foo ____" in result.stdout
    assert "Unresolvable JSON pointer: 'components/UnknownMethods'" in result.stdout


def test_direct_access(schema):
    # Then valid operations should be accessible via the mapping interface
    assert len(schema) == 1
    assert schema["/bar"]["GET"]


def test_state_machine(schema):
    # Then the generated state machine should include only valid operations
    machine = schema.as_state_machine()
    assert len(machine.bundles) == 1
    assert "GET" in machine.bundles["/bar"]
    assert len(machine.bundles["/bar"]) == 1
