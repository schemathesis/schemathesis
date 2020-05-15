import hypothesis
import pytest

from schemathesis._hypothesis import get_original_test


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
