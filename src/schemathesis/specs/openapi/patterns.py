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


@lru_cache
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
            anchor_length = _get_anchor_length(parsed[0][1])
            leading_anchor = pattern[:anchor_length]
            return leading_anchor + _update_quantifier(op, value, pattern[anchor_length:], min_length, max_length)
        if parsed[1][0] == ANCHOR:
            # Ends with an anchor
            op, value = parsed[0]
            anchor_length = _get_anchor_length(parsed[1][1])
            trailing_anchor = pattern[-anchor_length:]
            return _update_quantifier(op, value, pattern[:-anchor_length], min_length, max_length) + trailing_anchor
    elif len(parsed) == 3 and parsed[0][0] == ANCHOR and parsed[2][0] == ANCHOR:
        op, value = parsed[1]
        leading_anchor_length = _get_anchor_length(parsed[0][1])
        trailing_anchor_length = _get_anchor_length(parsed[2][1])
        leading_anchor = pattern[:leading_anchor_length]
        trailing_anchor = pattern[-trailing_anchor_length:]
        return (
            leading_anchor
            + _update_quantifier(
                op, value, pattern[leading_anchor_length:-trailing_anchor_length], min_length, max_length
            )
            + trailing_anchor
        )
    elif (
        len(parsed) > 3
        and parsed[0][0] == ANCHOR
        and parsed[-1][0] == ANCHOR
        and all(op == LITERAL or op in REPEATS for op, _ in parsed[1:-1])
    ):
        return _handle_anchored_pattern(parsed, pattern, min_length, max_length)
    return pattern


def _handle_anchored_pattern(parsed: list, pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update regex pattern with multiple quantified patterns to satisfy length constraints."""
    # Extract anchors
    leading_anchor_length = _get_anchor_length(parsed[0][1])
    trailing_anchor_length = _get_anchor_length(parsed[-1][1])
    leading_anchor = pattern[:leading_anchor_length]
    trailing_anchor = pattern[-trailing_anchor_length:]

    pattern_parts = parsed[1:-1]

    # Adjust length constraints by subtracting fixed literals length
    fixed_length = sum(1 for op, _ in pattern_parts if op == LITERAL)
    if min_length is not None:
        min_length -= fixed_length
        if min_length < 0:
            return pattern
    if max_length is not None:
        max_length -= fixed_length
        if max_length < 0:
            return pattern

    # Extract only min/max bounds from quantified parts
    quantifier_bounds = [value[:2] for op, value in pattern_parts if op in REPEATS]

    if not quantifier_bounds:
        return pattern

    length_distribution = _distribute_length_constraints(quantifier_bounds, min_length, max_length)
    if not length_distribution:
        return pattern

    # Rebuild pattern with updated quantifiers
    result = leading_anchor
    current_position = leading_anchor_length
    distribution_idx = 0

    for op, value in pattern_parts:
        if op == LITERAL:
            if pattern[current_position] == "\\":
                # Escaped value
                current_position += 2
                result += "\\"
            else:
                current_position += 1
            result += chr(value)
        else:
            new_min, new_max = length_distribution[distribution_idx]
            next_position = _find_quantified_end(pattern, current_position)
            quantified_segment = pattern[current_position:next_position]
            _, _, subpattern = value
            new_value = (new_min, new_max, subpattern)

            result += _update_quantifier(op, new_value, quantified_segment, new_min, new_max)
            current_position = next_position
            distribution_idx += 1

    return result + trailing_anchor


def _find_quantified_end(pattern: str, start: int) -> int:
    """Find the end position of current quantified part."""
    char_class_level = 0
    group_level = 0

    for i in range(start, len(pattern)):
        char = pattern[i]

        # Handle character class nesting
        if char == "[":
            char_class_level += 1
        elif char == "]":
            char_class_level -= 1

        # Handle group nesting
        elif char == "(":
            group_level += 1
        elif char == ")":
            group_level -= 1

        # Only process quantifiers when we're not inside any nested structure
        elif char_class_level == 0 and group_level == 0:
            if char in "*+?":
                return i + 1
            elif char == "{":
                # Find matching }
                while i < len(pattern) and pattern[i] != "}":
                    i += 1
                return i + 1

    return len(pattern)


def _distribute_length_constraints(
    bounds: list[tuple[int, int]], min_length: int | None, max_length: int | None
) -> list[tuple[int, int]] | None:
    """Distribute length constraints among quantified pattern parts."""
    # Handle exact length case with dynamic programming
    if min_length == max_length:
        assert min_length is not None
        target = min_length
        dp: dict[tuple[int, int], list[tuple[int, ...]] | None] = {}

        def find_valid_combination(pos: int, remaining: int) -> list[tuple[int, ...]] | None:
            if (pos, remaining) in dp:
                return dp[(pos, remaining)]

            if pos == len(bounds):
                return [()] if remaining == 0 else None

            max_len: int
            min_len, max_len = bounds[pos]
            if max_len == MAXREPEAT:
                max_len = remaining + 1
            else:
                max_len += 1

            # Try each possible length for current quantifier
            for length in range(min_len, max_len):
                rest = find_valid_combination(pos + 1, remaining - length)
                if rest is not None:
                    dp[(pos, remaining)] = [(length,) + r for r in rest]
                    return dp[(pos, remaining)]

            dp[(pos, remaining)] = None
            return None

        distribution = find_valid_combination(0, target)
        if distribution:
            return [(length, length) for length in distribution[0]]
        return None

    # Handle range case by distributing min/max bounds
    result = []
    remaining_min = min_length or 0
    remaining_max = max_length or MAXREPEAT

    for min_repeat, max_repeat in bounds:
        if remaining_min > 0:
            part_min = min(max_repeat, max(min_repeat, remaining_min))
        else:
            part_min = min_repeat

        if remaining_max < MAXREPEAT:
            part_max = min(max_repeat, remaining_max)
        else:
            part_max = max_repeat

        if part_min > part_max:
            return None

        result.append((part_min, part_max))

        remaining_min = max(0, remaining_min - part_min)
        remaining_max -= part_max if part_max != MAXREPEAT else 0

    if remaining_min > 0 or remaining_max < 0:
        return None

    return result


def _get_anchor_length(node_type: int) -> int:
    """Determine the length of the anchor based on its type."""
    if node_type in {sre.AT_BEGINNING_STRING, sre.AT_END_STRING, sre.AT_BOUNDARY, sre.AT_NON_BOUNDARY}:
        return 2  # \A, \Z, \b, or \B
    return 1  # ^ or $ or their multiline/locale/unicode variants


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
    return f"({_strip_quantifier(pattern).strip(')(')})" + _build_quantifier(min_length, max_length)


def _handle_literal_or_in_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Handle literal or character class quantifiers."""
    min_length = 1 if min_length is None else max(min_length, 1)
    return f"({pattern.strip(')(')})" + _build_quantifier(min_length, max_length)


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
    if pattern.endswith("}") and "{" in pattern:
        # Find the start of the exact quantifier and drop everything since that index
        idx = pattern.rfind("{")
        pattern = pattern[:idx]
    return pattern
