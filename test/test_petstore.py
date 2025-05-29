import pytest
from hypothesis import settings

from schemathesis.generation.modes import GenerationMode


@pytest.fixture(params=["petstore_v2.yaml", "petstore_v3.yaml"])
def testdir(request, testdir):
    def make_petstore_test(*args, **kwargs):
        kwargs["schema_name"] = request.param
        kwargs["generation_modes"] = [GenerationMode.POSITIVE]
        testdir.make_test(*args, **kwargs)

    testdir.make_petstore_test = make_petstore_test

    def assert_petstore(passed=1, tests_num=6, skipped=0):
        result = testdir.runpytest("-v", "-s")
        result.assert_outcomes(passed=passed, skipped=skipped)
        result.stdout.re_match_lines([rf"Hypothesis calls: {tests_num}"])

    testdir.assert_petstore = assert_petstore
    testdir.param = request.param  # `request.param` is not available in test for some reason

    return testdir


@pytest.fixture
def reload_profile():
    # Setting Hypothesis profile in a pytester-style test leads to overriding it globally
    yield
    settings.load_profile("default")


@pytest.mark.usefixtures("reload_profile")
def test_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/pet$").parametrize()
@settings(max_examples=5, deadline=None, suppress_health_check=list(HealthCheck))
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.body["name"])
    assert_list(case.body["photoUrls"])
    assert_requests_call(case)
"""
    )
    result = testdir.runpytest("-v", "-s", "--hypothesis-verbosity=verbose")
    result.assert_outcomes(passed=2)


def test_find_by_status(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/pet/findByStatus$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.query["status"])
    for item in case.query["status"]:
        assert item in ("available", "pending", "sold")
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=9)


def test_find_by_tag(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/pet/findByTags$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.query["tags"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=7)


def test_get_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="GET", path_regex="/pet/{petId}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["petId"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()


def test_update_pet(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="POST", path_regex="/pet/{petId}$").parametrize()
@settings(max_examples=5, deadline=None, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow])
def test_(request, case):
    assume(case.body is not NOT_SET)
    assume("name" in case.body)
    assume("status" in case.body)
    request.config.HYPOTHESIS_CASES += 1
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["petId"])
        assert_str(case.body["name"])
        assert_str(case.body["status"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()


def test_delete_pet(testdir):
    testdir.make_petstore_test(
        """
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.include(method="DELETE", path_regex="/pet/{petId}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["petId"])
        assert_str(case.headers["api_key"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()


def test_get_inventory(testdir):
    testdir.make_petstore_test(
        """
schema.config.generation.update(modes=[GenerationMode.POSITIVE])

@schema.include(path_regex="/store/inventory$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert not case.path_parameters
    assert case.body is NOT_SET
    assert not case.query
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=6)


def test_create_order(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/store/order$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=20)


def test_get_order(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="GET", path_regex="/store/order/{orderId}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["orderId"])
        assert case.path_parameters["orderId"] in range(1, 11)
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=9)


def test_delete_order(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="DELETE", path_regex="/store/order/{orderId}$").parametrize()
@settings(max_examples=5, suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow], deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    if not hasattr(case.meta.phase.data, "description"):
        assert_int(case.path_parameters["orderId"])
        assert case.path_parameters["orderId"] >= 1
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=7)


def test_create_user(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/user$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert isinstance(case.body, dict)
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=21)


def test_create_multiple_users(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/user/createWith").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_list(case.body)
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(2, 44)


def test_login(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/user/login").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.query["username"])
    assert_str(case.query["password"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()


def test_logout(testdir):
    testdir.make_petstore_test(
        """
@schema.include(path_regex="/user/logout").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert not case.path_parameters
    assert case.body is NOT_SET
    assert not case.query
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=2)


def test_get_user(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="GET", path_regex="/user/{username}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()


def test_update_user(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="PUT", path_regex="/user/{username}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
    assert isinstance(case.body, dict)
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore(tests_num=21)


def test_delete_user(testdir):
    testdir.make_petstore_test(
        """
@schema.include(method="DELETE", path_regex="/user/{username}$").parametrize()
@settings(max_examples=5, deadline=None)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert_str(case.path_parameters["username"])
    assert_requests_call(case)
"""
    )
    testdir.assert_petstore()
