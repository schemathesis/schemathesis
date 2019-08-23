def test_petstore(testdir):
    # Smoke test for petstore.yaml
    # TODO. Should be extended to verify that all data is generated correctly
    testdir.make_test(
        """
@schema.parametrize(max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
""",
        schema_name="petstore.yaml",
    )
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=20)
    # GET:/v2/store/inventory and GET:/v2/users/logout have only 1 case each,
    # then 18 * 5 + 2 = 92
    result.stdout.re_match_lines([r"Hypothesis calls: 92"])
