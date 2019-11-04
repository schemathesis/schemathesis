import sys
import warnings

import hypothesis
import pytest
from hypothesis.errors import NonInteractiveExampleWarning

from schemathesis._compat import handle_warnings
from schemathesis._hypothesis import get_original_test


def test_handle_warnings(recwarn):
    with handle_warnings():
        warnings.warn("Message", NonInteractiveExampleWarning)
    assert not recwarn


def test_handle_warnings_old_hypothesis(monkeypatch, recwarn):
    # Assume that there is an import error - old hypothesis version
    monkeypatch.setitem(sys.modules, "hypothesis.errors", None)
    with handle_warnings():
        warnings.warn("Message", NonInteractiveExampleWarning)
    assert recwarn


@pytest.mark.parametrize("version", ((4, 40, 0), (4, 42, 3)))
def test_get_original_test_old_hypothesis(monkeypatch, version):
    monkeypatch.setattr(hypothesis, "__version_info__", version)

    def original_test():
        pass

    def wrapped():
        pass

    # When old hypothesis wraps the original test function
    wrapped._hypothesis_internal_settings_applied = True
    wrapped._hypothesis_internal_test_function_without_warning = original_test

    # Then original test should be returned from the function
    assert get_original_test(wrapped) is original_test
    # And it should be no-op for not-wrapped tests
    assert get_original_test(original_test) is original_test


@pytest.mark.parametrize("version", ((4, 42, 4), (4, 43, 1)))
def test_get_original_test_new_hypothesis(monkeypatch, version):
    monkeypatch.setattr(hypothesis, "__version_info__", version)

    def original_test():
        pass

    original_test._hypothesis_internal_settings_applied = True
    assert get_original_test(original_test) is original_test
