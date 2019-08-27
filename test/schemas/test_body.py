from .utils import as_param


def test_default(testdir):
    # When parameter is specified for "body"
    testdir.make_test(
        """
@schema.parametrize(max_examples=3)
def test_(case):
    assert_int(case.body["id"])
        """,
        **as_param({"name": "id", "in": "body", "required": True, "schema": {"type": "integer"}})
    )
    # Then the generated test case should contain it its `body` attribute
    testdir.run_and_assert("-s", passed=1)
