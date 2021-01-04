from typing import TYPE_CHECKING, Callable, Dict, List, Tuple

import hypothesis.strategies as st
from requests.structures import CaseInsensitiveDict

from ....stateful import StepResult
from ..links import OpenAPILink, get_all_links
from ..utils import expand_status_code

if TYPE_CHECKING:
    from ....models import APIOperation

FilterFunction = Callable[[StepResult], bool]


def apply(
    operation: "APIOperation",
    bundles: Dict[str, CaseInsensitiveDict],
    connections: Dict[str, List[st.SearchStrategy[Tuple[StepResult, OpenAPILink]]]],
) -> None:
    """Gather all connections based on Open API links definitions."""
    all_status_codes = list(operation.definition.resolved["responses"])
    for status_code, link in get_all_links(operation):
        target_operation = link.get_target_operation()
        strategy = bundles[operation.path][operation.method.upper()].filter(
            make_response_filter(status_code, all_status_codes)
        )
        connections[target_operation.verbose_name].append(_convert_strategy(strategy, link))


def _convert_strategy(
    strategy: st.SearchStrategy[StepResult], link: OpenAPILink
) -> st.SearchStrategy[Tuple[StepResult, OpenAPILink]]:
    # This function is required to capture values properly (it won't work properly when lambda is defined in a loop)
    return strategy.map(lambda out: (out, link))


def make_response_filter(status_code: str, all_status_codes: List[str]) -> FilterFunction:
    """Create a filter for stored responses.

    This filter will decide whether some response is suitable to use as a source for requesting some API operation.
    """
    if status_code == "default":
        return default_status_code(all_status_codes)
    return match_status_code(status_code)


def match_status_code(status_code: str) -> FilterFunction:
    """Create a filter function that matches all responses with the given status code.

    Note that the status code can contain "X", which means any digit.
    For example, 50X will match all status codes from 500 to 509.
    """
    status_codes = set(expand_status_code(status_code))

    def compare(result: StepResult) -> bool:
        return result.response.status_code in status_codes

    # This name is displayed in the resulting strategy representation. For example, if you run your tests with
    # `--hypothesis-show-statistics`, then you can see `Bundle(name='GET /users/{user_id}').filter(match_200_response)`
    # which gives you information about the particularly used filter.
    compare.__name__ = f"match_{status_code}_response"

    return compare


def default_status_code(status_codes: List[str]) -> FilterFunction:
    """Create a filter that matches all "default" responses.

    In Open API, the "default" response is the one that is used if no other options were matched.
    Therefore we need to match only responses that were not matched by other listed status codes.
    """
    expanded_status_codes = {
        status_code for value in status_codes if value != "default" for status_code in expand_status_code(value)
    }

    def match_default_response(result: StepResult) -> bool:
        return result.response.status_code not in expanded_status_codes

    return match_default_response
