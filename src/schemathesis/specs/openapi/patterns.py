from __future__ import annotations

import re
from functools import lru_cache

from schemathesis.core.errors import InternalError


def is_valid_python_regex(pattern: str) -> bool:
    """Check if a pattern is valid Python regex."""
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


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
NOT_LITERAL = sre.NOT_LITERAL
IN = sre.IN
MAXREPEAT = sre_parse.MAXREPEAT


@lru_cache
def update_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update the quantifier of a regular expression based on given min and max lengths."""
    if not pattern or (min_length in (None, 0) and max_length is None):
        return pattern

    try:
        parsed = sre_parse.parse(pattern)
        updated = _handle_parsed_pattern(parsed, pattern, min_length, max_length)
        try:
            re.compile(updated)
        except re.error as exc:
            raise InternalError(
                f"The combination of min_length={min_length} and max_length={max_length} applied to the original pattern '{pattern}' resulted in an invalid regex: '{updated}'. "
                "This indicates a bug in the regex quantifier merging logic"
            ) from exc
        return updated
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
        # Special case for patterns canonicalisation. Some frameworks generate `\\w\\W` instead of `.`
        # Such patterns lead to significantly slower data generation
        if op == sre.IN and _matches_anything(value):
            op = sre.ANY
            value = None
            inner_pattern = "."
        elif op in REPEATS and len(value[2]) == 1 and value[2][0][0] == sre.IN and _matches_anything(value[2][0][1]):
            value = (value[0], value[1], [(sre.ANY, None)], *value[3:])
            inner_pattern = "."
        else:
            inner_pattern = pattern[leading_anchor_length:-trailing_anchor_length]
        # Single literal has the length of 1, but quantifiers could be != 1, which means we can't merge them
        if op == LITERAL and (
            (min_length is not None and min_length > 1) or (max_length is not None and max_length < 1)
        ):
            return pattern
        return leading_anchor + _update_quantifier(op, value, inner_pattern, min_length, max_length) + trailing_anchor
    elif (
        len(parsed) > 3
        and parsed[0][0] == ANCHOR
        and parsed[-1][0] == ANCHOR
        and all(op == LITERAL or op in REPEATS for op, _ in parsed[1:-1])
    ):
        return _handle_anchored_pattern(parsed, pattern, min_length, max_length)
    return pattern


def _matches_anything(value: list) -> bool:
    """Check if the given pattern is equivalent to '.' (match any character)."""
    # Common forms: [\w\W], [\s\S], etc.
    return value in (
        [(sre.CATEGORY, sre.CATEGORY_WORD), (sre.CATEGORY, sre.CATEGORY_NOT_WORD)],
        [(sre.CATEGORY, sre.CATEGORY_SPACE), (sre.CATEGORY, sre.CATEGORY_NOT_SPACE)],
        [(sre.CATEGORY, sre.CATEGORY_DIGIT), (sre.CATEGORY, sre.CATEGORY_NOT_DIGIT)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_WORD), (sre.CATEGORY, sre.CATEGORY_WORD)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_SPACE), (sre.CATEGORY, sre.CATEGORY_SPACE)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_DIGIT), (sre.CATEGORY, sre.CATEGORY_DIGIT)],
    )


def _handle_anchored_pattern(parsed: list, pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update regex pattern with multiple quantified patterns to satisfy length constraints."""
    # Extract anchors
    leading_anchor_length = _get_anchor_length(parsed[0][1])
    trailing_anchor_length = _get_anchor_length(parsed[-1][1])
    leading_anchor = pattern[:leading_anchor_length]
    trailing_anchor = pattern[-trailing_anchor_length:]

    pattern_parts = parsed[1:-1]

    # Calculate total fixed length and per-repetition lengths
    fixed_length = 0
    quantifier_bounds = []
    repetition_lengths = []

    for op, value in pattern_parts:
        if op in (LITERAL, NOT_LITERAL):
            fixed_length += 1
        elif op in REPEATS:
            min_repeat, max_repeat, subpattern = value
            quantifier_bounds.append((min_repeat, max_repeat))
            repetition_lengths.append(_calculate_min_repetition_length(subpattern))

    # Adjust length constraints by subtracting fixed literals length
    if min_length is not None:
        min_length -= fixed_length
        if min_length < 0:
            return pattern
    if max_length is not None:
        max_length -= fixed_length
        if max_length < 0:
            return pattern

    if not quantifier_bounds:
        return pattern

    length_distribution = _distribute_length_constraints(quantifier_bounds, repetition_lengths, min_length, max_length)
    if not length_distribution:
        return pattern

    # Rebuild pattern with updated quantifiers
    result = leading_anchor
    current_position = leading_anchor_length
    distribution_idx = 0

    for op, value in pattern_parts:
        if op == LITERAL:
            # Check if the literal comes from a bracketed expression,
            # e.g. Python regex parses "[+]" as a single LITERAL token.
            if pattern[current_position] == "[":
                # Find the matching closing bracket.
                end_idx = current_position + 1
                while end_idx < len(pattern):
                    # Check for an unescaped closing bracket.
                    if pattern[end_idx] == "]" and (end_idx == current_position + 1 or pattern[end_idx - 1] != "\\"):
                        end_idx += 1
                        break
                    end_idx += 1
                # Append the entire character set.
                result += pattern[current_position:end_idx]
                current_position = end_idx
                continue
            if pattern[current_position] == "\\":
                # Escaped value
                result += "\\"
                # Could be an octal value
                if (
                    current_position + 2 < len(pattern)
                    and pattern[current_position + 1] == "0"
                    and pattern[current_position + 2] in ("0", "1", "2", "3", "4", "5", "6", "7")
                ):
                    result += pattern[current_position + 1]
                    result += pattern[current_position + 2]
                    current_position += 3
                    continue
                current_position += 2
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
    bounds: list[tuple[int, int]], repetition_lengths: list[int], min_length: int | None, max_length: int | None
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

            max_repeat: int
            min_repeat, max_repeat = bounds[pos]
            repeat_length = repetition_lengths[pos]

            if max_repeat == MAXREPEAT:
                max_repeat = remaining // repeat_length + 1 if repeat_length > 0 else remaining + 1

            # Try each possible length for current quantifier
            for repeat_count in range(min_repeat, max_repeat + 1):
                used_length = repeat_count * repeat_length
                if used_length > remaining:
                    break

                rest = find_valid_combination(pos + 1, remaining - used_length)
                if rest is not None:
                    dp[(pos, remaining)] = [(repeat_count,) + r for r in rest]
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


def _calculate_min_repetition_length(subpattern: list) -> int:
    """Calculate minimum length contribution per repetition of a quantified group."""
    total = 0
    for op, value in subpattern:
        if op in [LITERAL, NOT_LITERAL, IN, sre.ANY]:
            total += 1
        elif op == sre.SUBPATTERN:
            _, _, _, inner_pattern = value
            total += _calculate_min_repetition_length(inner_pattern)
        elif op in REPEATS:
            min_repeat, _, inner_pattern = value
            inner_min = _calculate_min_repetition_length(inner_pattern)
            total += min_repeat * inner_min
    return total


def _get_anchor_length(node_type: int) -> int:
    """Determine the length of the anchor based on its type."""
    if node_type in {sre.AT_BEGINNING_STRING, sre.AT_END_STRING, sre.AT_BOUNDARY, sre.AT_NON_BOUNDARY}:
        return 2  # \A, \Z, \b, or \B
    return 1  # ^ or $ or their multiline/locale/unicode variants


def _update_quantifier(
    op: int, value: tuple | None, pattern: str, min_length: int | None, max_length: int | None
) -> str:
    """Update the quantifier based on the operation type and given constraints."""
    if op in REPEATS and value is not None:
        return _handle_repeat_quantifier(value, pattern, min_length, max_length)
    if op in (LITERAL, NOT_LITERAL, IN) and max_length != 0:
        return _handle_literal_or_in_quantifier(pattern, min_length, max_length)
    if op == sre.ANY and value is None:
        # Equivalent to `.` which is in turn is the same as `.{1}`
        return _handle_repeat_quantifier(
            SINGLE_ANY,
            pattern,
            min_length,
            max_length,
        )
    return pattern


SINGLE_ANY = sre_parse.parse(".{1}")[0][1]


def _handle_repeat_quantifier(
    value: tuple[int, int, tuple], pattern: str, min_length: int | None, max_length: int | None
) -> str:
    """Handle repeat quantifiers (e.g., '+', '*', '?')."""
    min_repeat, max_repeat, _ = value

    # First, analyze the inner pattern
    inner = _strip_quantifier(pattern)
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]

    # Determine the length of the inner pattern
    inner_length = 1  # default assumption for non-literal patterns
    try:
        parsed = sre_parse.parse(inner)
        if all(item[0] == LITERAL for item in parsed):
            inner_length = len(parsed)
            if max_length and max_length > 0 and inner_length > max_length:
                return pattern
    except re.error:
        pass

    if inner_length == 0:
        # Empty pattern contributes 0 chars regardless of repetitions
        # For length constraints, only 0 repetitions make sense
        if min_length is not None and min_length > 0:
            return pattern  # Can't satisfy positive length with empty pattern
        return f"({inner})" + _build_quantifier(0, 0)

    # Convert external length constraints to repetition constraints
    external_min_repeat = None
    external_max_repeat = None

    if min_length is not None:
        # Need at least ceil(min_length / inner_length) repetitions
        external_min_repeat = (min_length + inner_length - 1) // inner_length

    if max_length is not None:
        # Can have at most floor(max_length / inner_length) repetitions
        external_max_repeat = max_length // inner_length

    # Merge original repetition constraints with external constraints
    final_min_repeat = min_repeat
    if external_min_repeat is not None:
        final_min_repeat = max(min_repeat, external_min_repeat)

    final_max_repeat = max_repeat
    if external_max_repeat is not None:
        if max_repeat == MAXREPEAT:
            final_max_repeat = external_max_repeat
        else:
            final_max_repeat = min(max_repeat, external_max_repeat)

    if final_min_repeat > final_max_repeat:
        return pattern

    return f"({inner})" + _build_quantifier(final_min_repeat, final_max_repeat)


def _handle_literal_or_in_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Handle literal or character class quantifiers."""
    min_length = 1 if min_length is None else max(min_length, 1)
    if pattern.startswith("(") and pattern.endswith(")"):
        pattern = pattern[1:-1]
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
    for marker in ("*?", "+?", "??", "*+", "?+", "++"):
        if pattern.endswith(marker) and not pattern.endswith(rf"\{marker}"):
            return pattern[:-2]
    for marker in ("?", "*", "+"):
        if pattern.endswith(marker) and not pattern.endswith(rf"\{marker}"):
            pattern = pattern[:-1]
    if pattern.endswith("}") and "{" in pattern:
        # Find the start of the exact quantifier and drop everything since that index
        idx = pattern.rfind("{")
        pattern = pattern[:idx]
    return pattern
