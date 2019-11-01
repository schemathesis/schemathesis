import sys
import warnings

import hypothesis
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


def test_get_original_test_old_hypothesis(monkeypatch):
    monkeypatch.setattr(hypothesis, "__version_info__", (4, 40, 0))

    def original_test():
        pass

    def wrapped():
        pass

    wrapped._hypothesis_internal_settings_applied = True
    wrapped._hypothesis_internal_test_function_without_warning = original_test

    assert get_original_test(wrapped) is original_test
