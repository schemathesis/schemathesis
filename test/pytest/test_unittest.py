import os
import sys

import pytest

from ..utils import HERE, has_hypothesis_failure_header


@pytest.fixture(autouse=True)
def pythonpath_fix(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.path.dirname(HERE))


def test_unittest_success_and_failure(testdir):
    module = testdir.make_test(
        """
from unittest import TestCase

class TestSchema(TestCase):

    @given(case=schema["/users"]["GET"].as_strategy())
    def test_passes(self, case):
        assert case.method == "GET"

    @given(case=schema["/users"]["GET"].as_strategy())
    def test_fails(self, case):
        assert 0
"""
    )
    result = testdir.run(sys.executable, "-m", "unittest", str(module))
    assert result.ret == 1
    result.stderr.re_match_lines([".* assert 0.*", "Ran 2 tests in.*", "FAILED \\(failures=1\\)"])
    assert has_hypothesis_failure_header(result.stderr.str())
