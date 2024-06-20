from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from ..hooks import HookContext, register, unregister

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from ..models import Case


def install() -> None:
    warnings.warn(
        "The `--contrib-unique-data` CLI option and the corresponding `schemathesis.contrib.unique_data` hook "
        "are **DEPRECATED**. The concept of this feature does not fit the core principles of Hypothesis where "
        "strategies are configurable on a per-example basis but this feature implies uniqueness across examples. "
        "This leads to cryptic error messages about external state and flaky test runs, "
        "therefore it will be removed in Schemathesis 4.0",
        DeprecationWarning,
        stacklevel=1,
    )
    register(before_generate_case)


def uninstall() -> None:
    unregister(before_generate_case)


def before_generate_case(context: HookContext, strategy: st.SearchStrategy[Case]) -> st.SearchStrategy[Case]:
    seen = set()

    def is_not_seen(case: Case) -> bool:
        # Calculate hash just once as it is costly
        hashed = hash(case)
        if hashed not in seen:
            seen.add(hashed)
            return True
        return False

    return strategy.filter(is_not_seen)
