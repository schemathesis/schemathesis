from __future__ import annotations

import re
from functools import lru_cache

try:  # pragma: no cover
    import re._constants as sre
    import re._parser as sre_parse
except ImportError:
    import sre_constants as sre
    import sre_parse

ANCHOR = sre.AT
REPEATS: tuple
if hasattr(sre, "POSSESSIVE_REPEAT"):
    REPEATS = (sre.MIN_REPEAT, sre.MAX_REPEAT, sre.POSSESSIVE_REPEAT)
else:
    REPEATS = (sre.MIN_REPEAT, sre.MAX_REPEAT)
LITERAL = sre.LITERAL
IN = sre.IN
MAXREPEAT = sre_parse.MAXREPEAT


@lru_cache()
def update_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update the quantifier of a regular expression based on given min and max lengths."""
    if not pattern or (min_length in (None, 0) and max_length is None):
        return pattern

    try:
        parsed = sre_parse.parse(pattern)
        return _handle_parsed_pattern(parsed, pattern, min_length, max_length)
    except re.error:
        # Invalid pattern
        return pattern


def _handle_parsed_pattern(parsed: list, pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Handle the parsed pattern and update quantifiers based on different cases."""
    if len(parsed) == 1:
        op, value = parsed[0]
        return _update_quantifier(op, value, pattern, min_length, max_length)
    elif len(parsed) == 2:
        if parsed[0][0] == ANCHOR:
            # Starts with an anchor
            op, value = parsed[1]
            leading_anchor = pattern[0]
            return leading_anchor + _update_quantifier(op, value, pattern[1:], min_length, max_length)
        if parsed[1][0] == ANCHOR:
            # Ends with an anchor
            op, value = parsed[0]
            trailing_anchor = pattern[-1]
            return _update_quantifier(op, value, pattern[:-1], min_length, max_length) + trailing_anchor
    elif len(parsed) == 3 and parsed[0][0] == ANCHOR and parsed[2][0] == ANCHOR:
        op, value = parsed[1]
        leading_anchor = pattern[0]
        trailing_anchor = pattern[-1]
        return leading_anchor + _update_quantifier(op, value, pattern[1:-1], min_length, max_length) + trailing_anchor
    return pattern


def _update_quantifier(op: int, value: tuple, pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update the quantifier based on the operation type and given constraints."""
    if op in REPEATS:
        return _handle_repeat_quantifier(value, pattern, min_length, max_length)
    if op in (LITERAL, IN) and max_length != 0:
        return _handle_literal_or_in_quantifier(pattern, min_length, max_length)
    return pattern


def _handle_repeat_quantifier(
    value: tuple[int, int, tuple], pattern: str, min_length: int | None, max_length: int | None
) -> str:
    """Handle repeat quantifiers (e.g., '+', '*', '?')."""
    min_repeat, max_repeat, _ = value
    min_length, max_length = _build_size(min_repeat, max_repeat, min_length, max_length)
    if min_length > max_length:
        return pattern
    return f"({_strip_quantifier(pattern)})" + _build_quantifier(min_length, max_length)


def _handle_literal_or_in_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Handle literal or character class quantifiers."""
    min_length = 1 if min_length is None else max(min_length, 1)
    return f"({pattern})" + _build_quantifier(min_length, max_length)


def _build_quantifier(minimum: int | None, maximum: int | None) -> str:
    """Construct a quantifier string based on min and max values."""
    if maximum == MAXREPEAT or maximum is None:
        return f"{{{minimum or 0},}}"
    if minimum == maximum:
        return f"{{{minimum}}}"
    return f"{{{minimum or 0},{maximum}}}"


def _build_size(min_repeat: int, max_repeat: int, min_length: int | None, max_length: int | None) -> tuple[int, int]:
    """Merge the current repetition constraints with the provided min and max lengths."""
    if min_length is not None:
        min_repeat = max(min_repeat, min_length)
    if max_length is not None:
        if max_repeat == MAXREPEAT:
            max_repeat = max_length
        else:
            max_repeat = min(max_repeat, max_length)
    return min_repeat, max_repeat


def _strip_quantifier(pattern: str) -> str:
    """Remove quantifier from the pattern."""
    # Lazy & posessive quantifiers
    if pattern.endswith(("*?", "+?", "??", "*+", "?+", "++")):
        return pattern[:-2]
    if pattern.endswith(("?", "*", "+")):
        pattern = pattern[:-1]
    if pattern.endswith("}"):
        # Find the start of the exact quantifier and drop everything since that index
        idx = pattern.rfind("{")
        pattern = pattern[:idx]
    return pattern
