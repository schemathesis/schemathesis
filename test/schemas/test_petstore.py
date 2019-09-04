import pytest


@pytest.fixture
def testdir(testdir):
    def make_petstore_test(*args, **kwargs):
        kwargs["schema_name"] = "petstore.yaml"
        testdir.make_test(*args, **kwargs)

    testdir.make_petstore_test = make_petstore_test

    def assert_petstore(passed=1, tests_num=5):
        result = testdir.runpytest("-v")
        result.assert_outcomes(passed=passed)
        result.stdout.re_match_lines([rf"Hypothesis calls: {tests_num}"])

    testdir.assert_petstore = assert_petstore

    return testdir


def test_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/pet$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.body["name"])
    assert_list(case.body["photoUrls"])
"""
    )
    testdir.assert_petstore(2, 10)


def test_find_by_status(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/pet/findByStatus$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.query["status"])
    if case.query["status"]:
        assert case.query["status"][0] in ("available", "pending", "sold")
"""
    )
    testdir.assert_petstore()


def test_find_by_tag(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/pet/findByTags$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.query["tags"])
"""
    )
    testdir.assert_petstore()


def test_get_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="GET", filter_endpoint="/pet/{petId}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.path_parameters["petId"])
"""
    )
    testdir.assert_petstore()


@pytest.mark.xfail(reason="formData generation is not implemented", run=False)
def test_update_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="POST", filter_endpoint="/pet/{petId}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.form_data["petId"])  # TODO. Or save it in body?
"""
    )
    testdir.assert_petstore()


def test_delete_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="DELETE", filter_endpoint="/pet/{petId}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.path_parameters["petId"])
"""
    )
    # TODO. verify header value once it is implemented
    testdir.assert_petstore()


@pytest.mark.xfail(reason="formData generation is not implemented", run=False)
def test_upload_image(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/pet/{petId}/uploadImage$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.path_parameters["petId"])
"""
    )
    testdir.assert_petstore()


def test_get_inventory(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/store/inventory$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert not case.path_parameters
    assert not case.body
    assert not case.query
"""
    )
    testdir.assert_petstore(tests_num=1)


def test_create_order(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/store/order$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
"""
    )
    testdir.assert_petstore()


def test_get_order(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="GET", filter_endpoint="/store/order/{orderId}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.path_parameters["orderId"])
    assert case.path_parameters["orderId"] in range(1, 11)
"""
    )
    testdir.assert_petstore()


def test_delete_order(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="DELETE", filter_endpoint="/store/order/{orderId}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_int(case.path_parameters["orderId"])
    assert case.path_parameters["orderId"] >= 1
"""
    )
    testdir.assert_petstore()


def test_create_user(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/user$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert isinstance(case.body, dict)
"""
    )
    testdir.assert_petstore()


def test_create_multiple_users(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/user/createWith", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.body)
"""
    )
    testdir.assert_petstore(2, 10)


def test_login(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/user/login", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.query["username"])
    assert_str(case.query["password"])
"""
    )
    testdir.assert_petstore()


def test_logout(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_endpoint="/user/logout", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert not case.path_parameters
    assert not case.body
    assert not case.query
"""
    )
    testdir.assert_petstore(tests_num=1)


def test_get_user(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="GET", filter_endpoint="/user/{username}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
"""
    )
    testdir.assert_petstore()


def test_update_user(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="PUT", filter_endpoint="/user/{username}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
    assert isinstance(case.body, dict)
"""
    )
    testdir.assert_petstore()


def test_delete_user(testdir):
    testdir.make_petstore_test(
        """
@schema.parametrize(filter_method="DELETE", filter_endpoint="/user/{username}$", max_examples=5)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
"""
    )
    testdir.assert_petstore()
