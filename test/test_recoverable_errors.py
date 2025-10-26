import pytest

import schemathesis

# See GH-999
# Tests for behavior when the API schema contains operations that are valid but contains an unresolvable reference
# Note that these errors can't be detected with meta-schema validation


@pytest.fixture
def schema(open_api_3_schema_with_recoverable_errors):
    return schemathesis.openapi.from_dict(open_api_3_schema_with_recoverable_errors)


EXPECTED_OUTPUT_LINES = [
    # Path-level error. no method is displayed
    r".*test_\[/foo\] FAILED",
    # Valid operation
    r".*test_\[GET /bar\] PASSED",
    # Operation-level error
    r".*test_\[POST /bar\] FAILED",
    # The error in both failing cases
    ".*InvalidSchema: Unresolvable reference in the schema.*",
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


def test_in_pytest_subtests(testdir, open_api_3_schema_with_recoverable_errors):
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

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
    result.stdout.re_match_lines(
        [
            # Path-level error. no method is displayed
            r".*test_\[/foo\] \(path='/foo'\) SUBFAIL",
            # Valid operation
            r".*test_\[GET /bar\] \(label='GET /bar'\) SUBPASS",
            # Operation-level error
            r".*test_\[POST /bar\] \(method='POST', path='/bar'\) SUBFAIL",
            # The error in both failing cases
            ".*Unresolvable reference in the schema.*",
        ]
    )


def test_jsonschema_error(testdir, openapi_3_schema_with_invalid_security):
    testdir.make_test(
        """
lazy_schema = schemathesis.pytest.from_fixture("simple_schema")

@lazy_schema.parametrize()
@settings(max_examples=1)
def test_(case):
    pass
    """,
        schema=openapi_3_schema_with_invalid_security,
    )
    result = testdir.runpytest()
    # Then valid operation should be tested
    # And errors on the single operation error should be displayed
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [
            ".*InvalidSchema: Invalid Security Scheme Object definition for `bearerAuth`",
        ]
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_in_cli(ctx, cli, open_api_3_schema_with_recoverable_errors, workers, openapi3_base_url, snapshot_cli):
    schema_path = ctx.makefile(open_api_3_schema_with_recoverable_errors)
    # Then valid operation should be tested
    # And errors on the single operation error should be displayed
    assert (
        cli.run(str(schema_path), f"--workers={workers}", f"--url={openapi3_base_url}", "-c not_a_server_error")
        == snapshot_cli
    )


def test_direct_access(schema):
    # Then valid operations should be accessible via the mapping interface
    assert len(schema) == 2
    assert schema["/bar"]["GET"]


def test_state_machine(schema):
    # Then the generated state machine should include only valid operations
    machine = schema.as_state_machine()
    assert len(machine.bundles) == 0
