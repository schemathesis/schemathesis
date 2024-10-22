import pytest

from schemathesis.internal.checks import wrap_check


def test_wrap_check_new_style():
    def new_style_check(ctx, response, case):
        return True

    wrapped = wrap_check(new_style_check)
    assert wrapped is new_style_check


def test_wrap_check_old_style():
    def old_style_check(response, case):
        return True

    with pytest.warns(DeprecationWarning, match="uses an outdated signature"):
        wrapped = wrap_check(old_style_check)

    assert wrapped is not old_style_check
    assert wrapped(None, None, None) is True


def test_wrap_check_invalid():
    def invalid_check(arg1): ...

    with pytest.raises(ValueError, match="Invalid check function signature"):
        wrap_check(invalid_check)


def test_wrap_check_too_many_args():
    def too_many_args(arg1, arg2, arg3, arg4): ...

    with pytest.raises(ValueError, match="Invalid check function signature"):
        wrap_check(too_many_args)


def test_wrap_check_compatibility_wrapper(response_factory):
    def old_style_check(response, case):
        return response.status_code == 200

    wrapped = wrap_check(old_style_check)

    response = response_factory.requests()

    assert wrapped(None, response, None) is True

    response.status_code = 404
    assert wrapped(None, response, None) is False
