from typing import Any, Callable, Generator, List, Optional

import hypothesis
import hypothesis.errors
import pytest
from _pytest import nodes
from _pytest.python import Function, PyCollector  # type: ignore

from ..generator import get_case_strategy
from ..parametrizer import is_schemathesis_test
from ..schemas import Endpoint


def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Optional["SchemathesisCase"]:
    """Switch to a different collector if the test is wrapped with `SchemaParametrizer.parametrize()`."""
    if is_schemathesis_test(obj):
        return SchemathesisCase(obj, name, collector)
    return None


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, *args: Any, **kwargs: Any) -> None:
        self.test_function = test_function
        self.schemathesis_case = test_function._schema_parametrizer  # type: ignore
        super().__init__(*args, **kwargs)

    def make_hypothesis_item(self, endpoint: Endpoint) -> Callable:
        """Create a Hypothesis test."""
        strategy = get_case_strategy(endpoint)
        item = hypothesis.given(case=strategy)(self.test_function)
        return hypothesis.settings(**self.schemathesis_case.hypothesis_settings)(item)

    def _gen_items(self, endpoint: Endpoint) -> Generator[Function, None, None]:
        """Generate all items for the given endpoint.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.
        """
        # TODO. exclude it early, if tests are deselected, then hypothesis strategies will not be used anyway
        # But, it is important to know the total number of tests for all method/endpoint combos
        hypothesis_item = self.make_hypothesis_item(endpoint)
        name = self.name + f"[{endpoint.method}:{endpoint.path}]"
        items = self.ihook.pytest_pycollect_makeitem(collector=self.parent, name=name, obj=hypothesis_item)
        for item in items:
            # Move to collect hook?
            item.__class__ = CustomFunc
            item.obj = hypothesis_item
            yield item

    def collect(self) -> List[Function]:
        """Generate different test items for all endpoints available in the given schema."""
        return [
            item
            for endpoint in self.schemathesis_case.schema.get_all_endpoints(
                filter_method=self.schemathesis_case.filter_method,
                filter_endpoint=self.schemathesis_case.filter_endpoint,
            )
            for item in self._gen_items(endpoint)
        ]


class CustomFunc(Function):
    def runtest(self):
        try:
            super().runtest()
        except hypothesis.errors.InvalidArgument as exc:
            # Make it more visible? Maybe suppress logging upper?
            pytest.skip(exc.args[0])
