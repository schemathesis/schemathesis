from textwrap import dedent

import pytest

from .utils import make_schema


@pytest.fixture()
def testdir(testdir):
    def maker(content, **kwargs):
        schema = make_schema(**kwargs)
        preparation = dedent(
            """
        import pytest
        from schemathesis import Parametrizer
        from test.schemas.utils import *
        schema = Parametrizer({schema})
        """.format(
                schema=schema
            )
        )
        testdir.makepyfile(preparation, content)
        testdir.makepyfile(
            conftest=dedent(
                """
        def pytest_configure(config):
            config.HYPOTHESIS_CASES = 0
        def pytest_unconfigure(config):
            print(f"Hypothesis calls: {config.HYPOTHESIS_CASES}")
        """
            )
        )

    testdir.make_test = maker

    def run_and_assert(*args, **kwargs):
        result = testdir.runpytest(*args)
        result.assert_outcomes(**kwargs)

    testdir.run_and_assert = run_and_assert

    return testdir
