from typing import TYPE_CHECKING

from hypothesis import strategies as st

from ..hooks import HookContext, register, unregister

if TYPE_CHECKING:
    from ..models import Case


def install() -> None:
    register(before_generate_case)


def uninstall() -> None:
    unregister(before_generate_case)


def before_generate_case(context: HookContext, strategy: st.SearchStrategy["Case"]) -> st.SearchStrategy["Case"]:
    seen = set()

    def is_not_seen(case: "Case") -> bool:
        # Calculate hash just once as it is costly
        hashed = hash(case)
        if hashed not in seen:
            seen.add(hashed)
            return True
        return False

    return strategy.filter(is_not_seen)
