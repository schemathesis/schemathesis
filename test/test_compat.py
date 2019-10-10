import sys
import warnings

from hypothesis.errors import NonInteractiveExampleWarning

from schemathesis._compat import handle_warnings


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
