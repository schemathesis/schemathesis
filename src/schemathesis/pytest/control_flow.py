from typing import NoReturn

import pytest


def fail_on_no_matches(node_id: str) -> NoReturn:  # type: ignore[misc]
    pytest.fail(f"Test function {node_id} does not match any API operations and therefore has no effect")
