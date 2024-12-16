import os
import sys

import pytest

from .utils import HERE


@pytest.fixture(autouse=True)
def pythonpath_fix(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.path.dirname(HERE))


def test_unittest_success(testdir):
    module = testdir.make_test(
        """
from unittest import TestCase

class TestSchema(TestCase):

    @given(case=schema["/users"]["GET"].as_strategy())
    def test_something(self, case):
        assert case.method == "GET"
"""
    )
    result = testdir.run(sys.executable, "-m", "unittest", str(module))
    assert result.ret == 0
    result.stderr.re_match_lines(["Ran 1 test in.*", "OK"])


def test_unittest_failure(testdir):
    module = testdir.make_test(
        """
from unittest import TestCase

class TestSchema(TestCase):

    @given(case=schema["/users"]["GET"].as_strategy())
    def test_something(self, case):
        assert 0
"""
    )
    result = testdir.run(sys.executable, "-m", "unittest", str(module))
    assert result.ret == 1
    result.stderr.re_match_lines([".* assert 0.*", "FAILED (failures=1)"])
    result.stderr.re_match_lines(["Falsifying example: .*"])
