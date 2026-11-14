from schemathesis.pytest.plugin import SchemaHandleMark


def test_is_schemathesis_test(swagger_20):
    @swagger_20.parametrize()
    def test():
        pass

    assert SchemaHandleMark.is_set(test)
