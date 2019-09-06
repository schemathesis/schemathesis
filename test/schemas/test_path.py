def test_default(testdir):
    # When parameter is specified for "path"
    testdir.make_test(
        """
@schema.parametrize(filter_endpoint="/users/{user_id}")
@settings(max_examples=3)
def test_(case):
    assert_int(case.path_parameters["user_id"])
        """,
        paths={
            "/users/{user_id}": {
                "get": {"parameters": [{"name": "user_id", "required": True, "in": "path", "type": "integer"}]}
            }
        },
    )
    # Then the generated test case should contain it its `body` attribute
    testdir.run_and_assert(passed=1)
