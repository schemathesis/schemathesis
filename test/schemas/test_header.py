from .utils import as_param


def test_default(testdir):
    # When parameter is specified for "header"
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(case):
    assert_str(case.headers["api_key"])
        """,
        **as_param({"name": "api_key", "in": "header", "required": True, "type": "string"}),
    )
    # Then the generated test case should contain it in its `headers` attribute
    testdir.run_and_assert("-s", passed=1)
