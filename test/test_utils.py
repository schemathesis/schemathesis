from schemathesis.utils import is_schemathesis_test


def test_is_schemathesis_test(swagger_20):
    # When a test is wrapped with `parametrize`

    @swagger_20.parametrize()
    def test():
        pass

    # Then it should be recognized as a schemathesis test
    assert is_schemathesis_test(test)
