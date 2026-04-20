from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Literal, TypeAlias

from schemathesis.core.errors import InternalError

# Unicode property escape translations (PCRE/Java/JS -> Python approximations)
# These cover Latin-based scripts which handle the majority of real-world APIs
_LETTER_CLASS = r"a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F"
_LETTER_UPPER_CLASS = r"A-Z\u00C0-\u00D6\u00D8-\u00DE\u0100-\u0136\u0139-\u0147\u014A-\u0178\u0179-\u017D"
_LETTER_LOWER_CLASS = r"a-z\u00DF-\u00F6\u00F8-\u00FF\u0101-\u0137\u013A-\u0148\u014B-\u0177\u017A-\u017E"
_OTHER_LETTER_CLASS = r"\u00AA\u00BA\u01BB\u01C0-\u01C3\u0294"
_DIGIT_CLASS = r"0-9"
_ALNUM_CLASS = rf"{_LETTER_CLASS}{_DIGIT_CLASS}"
_SPACE_CLASS = r" \t\n\r\f\v"
_XDIGIT_CLASS = r"0-9A-Fa-f"
_ASCII_CLASS = r"\x00-\x7F"
# Punctuation: ASCII + Latin-1 Supplement + General Punctuation
_PUNCT_CLASS = r"!\"#%&'()*,\-./:;?@\[\\\]_{}\u00A1\u00A7\u00AB\u00B6\u00B7\u00BB\u00BF\u2010-\u2027\u2030-\u203E"
# Combining marks (common diacritical marks)
_MARK_CLASS = r"\u0300-\u036F\u0483-\u0489\u1DC0-\u1DFF\u20D0-\u20FF"
# Symbols: currency, math, misc symbols
_SYMBOL_CLASS = r"$+<=>^`|~\u00A2-\u00A6\u00A8\u00A9\u00AC\u00AE-\u00B1\u00B4\u00D7\u00F7\u2200-\u22FF\u2600-\u26FF"
# Control characters
_CONTROL_CLASS = r"\x00-\x1F\x7F-\x9F"
# Graph (visible characters) and Print (graph + space)
_GRAPH_CLASS = r"!-~\u00A1-\u00AC\u00AE-\u00FF"
_PRINT_CLASS = rf" {_GRAPH_CLASS}"
# Blank (horizontal whitespace)
_BLANK_CLASS = r" \t"

# Order matters - check bracketed forms first to avoid double-bracketing
_UNICODE_PROPERTY_MAP = (
    # Bracketed forms (must come first)
    (r"[\p{L}]", f"[{_LETTER_CLASS}]"),
    (r"[\p{Lu}]", f"[{_LETTER_UPPER_CLASS}]"),
    (r"[\p{Ll}]", f"[{_LETTER_LOWER_CLASS}]"),
    (r"[\p{Lo}]", f"[{_OTHER_LETTER_CLASS}]"),
    (r"[\p{N}]", f"[{_DIGIT_CLASS}]"),
    (r"[\p{Nd}]", f"[{_DIGIT_CLASS}]"),
    (r"[\p{Alpha}]", f"[{_LETTER_CLASS}]"),
    (r"[\p{Digit}]", f"[{_DIGIT_CLASS}]"),
    (r"[\p{XDigit}]", f"[{_XDIGIT_CLASS}]"),
    (r"[\p{Alnum}]", f"[{_ALNUM_CLASS}]"),
    (r"[\p{Space}]", f"[{_SPACE_CLASS}]"),
    (r"[\p{Z}]", f"[{_SPACE_CLASS}]"),
    (r"[\p{Zs}]", f"[{_SPACE_CLASS}]"),
    (r"[\p{P}]", f"[{_PUNCT_CLASS}]"),
    (r"[\p{Punct}]", f"[{_PUNCT_CLASS}]"),
    (r"[\p{M}]", f"[{_MARK_CLASS}]"),
    (r"[\p{S}]", f"[{_SYMBOL_CLASS}]"),
    (r"[\p{C}]", f"[{_CONTROL_CLASS}]"),
    (r"[\p{Cntrl}]", f"[{_CONTROL_CLASS}]"),
    (r"[\p{ASCII}]", f"[{_ASCII_CLASS}]"),
    (r"[\p{Graph}]", f"[{_GRAPH_CLASS}]"),
    (r"[\p{Print}]", f"[{_PRINT_CLASS}]"),
    (r"[\p{Blank}]", f"[{_BLANK_CLASS}]"),
    (r"[\p{Upper}]", f"[{_LETTER_UPPER_CLASS}]"),
    (r"[\p{IsLetter}]", f"[{_LETTER_CLASS}]"),
    (r"[\P{L}]", f"[^{_LETTER_CLASS}]"),
    (r"[\P{N}]", f"[^{_DIGIT_CLASS}]"),
    (r"[\P{Nd}]", f"[^{_DIGIT_CLASS}]"),
    (r"[\P{C}]", f"[^{_CONTROL_CLASS}]"),
    (r"[\P{M}]", f"[^{_MARK_CLASS}]"),
    # Shorthand forms in brackets (single-letter properties without braces)
    (r"[\pL]", f"[{_LETTER_CLASS}]"),
    (r"[\pN]", f"[{_DIGIT_CLASS}]"),
    (r"[\pP]", f"[{_PUNCT_CLASS}]"),
    (r"[\pM]", f"[{_MARK_CLASS}]"),
    (r"[\pS]", f"[{_SYMBOL_CLASS}]"),
    (r"[\pC]", f"[{_CONTROL_CLASS}]"),
    (r"[\pZ]", f"[{_SPACE_CLASS}]"),
    (r"[\PL]", f"[^{_LETTER_CLASS}]"),
    (r"[\PN]", f"[^{_DIGIT_CLASS}]"),
    (r"[\PC]", f"[^{_CONTROL_CLASS}]"),
    (r"[\PM]", f"[^{_MARK_CLASS}]"),
    # Standalone forms with braces
    (r"\p{L}", f"[{_LETTER_CLASS}]"),
    (r"\p{Lu}", f"[{_LETTER_UPPER_CLASS}]"),
    (r"\p{Ll}", f"[{_LETTER_LOWER_CLASS}]"),
    (r"\p{Lo}", f"[{_OTHER_LETTER_CLASS}]"),
    (r"\p{N}", f"[{_DIGIT_CLASS}]"),
    (r"\p{Nd}", f"[{_DIGIT_CLASS}]"),
    (r"\p{Alpha}", f"[{_LETTER_CLASS}]"),
    (r"\p{Digit}", f"[{_DIGIT_CLASS}]"),
    (r"\p{XDigit}", f"[{_XDIGIT_CLASS}]"),
    (r"\p{Alnum}", f"[{_ALNUM_CLASS}]"),
    (r"\p{Space}", f"[{_SPACE_CLASS}]"),
    (r"\p{Z}", f"[{_SPACE_CLASS}]"),
    (r"\p{Zs}", f"[{_SPACE_CLASS}]"),
    (r"\p{P}", f"[{_PUNCT_CLASS}]"),
    (r"\p{Punct}", f"[{_PUNCT_CLASS}]"),
    (r"\p{M}", f"[{_MARK_CLASS}]"),
    (r"\p{S}", f"[{_SYMBOL_CLASS}]"),
    (r"\p{C}", f"[{_CONTROL_CLASS}]"),
    (r"\p{Cntrl}", f"[{_CONTROL_CLASS}]"),
    (r"\p{ASCII}", f"[{_ASCII_CLASS}]"),
    (r"\p{Graph}", f"[{_GRAPH_CLASS}]"),
    (r"\p{Print}", f"[{_PRINT_CLASS}]"),
    (r"\p{Blank}", f"[{_BLANK_CLASS}]"),
    (r"\p{Upper}", f"[{_LETTER_UPPER_CLASS}]"),
    (r"\p{IsLetter}", f"[{_LETTER_CLASS}]"),
    (r"\P{L}", f"[^{_LETTER_CLASS}]"),
    (r"\P{N}", f"[^{_DIGIT_CLASS}]"),
    (r"\P{Nd}", f"[^{_DIGIT_CLASS}]"),
    (r"\P{C}", f"[^{_CONTROL_CLASS}]"),
    (r"\P{M}", f"[^{_MARK_CLASS}]"),
    # Shorthand standalone forms (single-letter properties without braces)
    (r"\pL", f"[{_LETTER_CLASS}]"),
    (r"\pN", f"[{_DIGIT_CLASS}]"),
    (r"\pP", f"[{_PUNCT_CLASS}]"),
    (r"\pM", f"[{_MARK_CLASS}]"),
    (r"\pS", f"[{_SYMBOL_CLASS}]"),
    (r"\pC", f"[{_CONTROL_CLASS}]"),
    (r"\pZ", f"[{_SPACE_CLASS}]"),
    (r"\PL", f"[^{_LETTER_CLASS}]"),
    (r"\PN", f"[^{_DIGIT_CLASS}]"),
    (r"\PC", f"[^{_CONTROL_CLASS}]"),
    (r"\PM", f"[^{_MARK_CLASS}]"),
)


@lru_cache(maxsize=256)
def normalize_regex(pattern: object) -> str | None:
    r"""Translate PCRE-style Unicode property escapes and Python-specific anchors.

    Handles:
    - PCRE Unicode property escapes (\p{L}, \pL, etc.) -> Python equivalents
    - Python anchors (\A, \Z) -> Rust-compatible equivalents (^, $)

    Returns the translated pattern if successful, None if translation failed
    or the result is not a valid Python regex.
    """
    if not isinstance(pattern, str):
        return None
    # Check for both braced (\p{L}) and shorthand (\pL) forms
    has_braced = r"\p{" in pattern or r"\P{" in pattern
    has_shorthand = any(
        esc in pattern
        for esc in (r"\pL", r"\pN", r"\pP", r"\pM", r"\pS", r"\pC", r"\pZ", r"\PL", r"\PN", r"\PC", r"\PM")
    )
    # Check for Python-specific anchors that need Rust translation
    has_python_anchors = pattern.startswith(r"\A") or pattern.endswith(r"\Z")

    if not has_braced and not has_shorthand and not has_python_anchors:
        return None  # No translation needed

    translated = pattern
    for pcre_escape, python_equiv in _UNICODE_PROPERTY_MAP:
        translated = translated.replace(pcre_escape, python_equiv)

    # Check if there are still untranslated Unicode property escapes
    if r"\p{" in translated or r"\P{" in translated:
        return None  # Contains unsupported escapes

    # Translate Python-specific anchors to Rust equivalents for jsonschema-rs
    if translated.startswith(r"\A"):
        translated = "^" + translated[2:]
    if translated.endswith(r"\Z"):
        translated = translated[:-2] + "$"

    # Verify the translated pattern is valid
    if is_valid_python_regex(translated):
        return translated
    return None


def is_valid_python_regex(pattern: object) -> bool:
    """Check if a pattern is valid Python regex."""
    if not isinstance(pattern, str):
        return False
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
REPEATS: tuple[int, ...]
if hasattr(sre, "POSSESSIVE_REPEAT"):
    REPEATS = (sre.MIN_REPEAT, sre.MAX_REPEAT, sre.POSSESSIVE_REPEAT)
else:
    REPEATS = (sre.MIN_REPEAT, sre.MAX_REPEAT)
LITERAL = sre.LITERAL
NOT_LITERAL = sre.NOT_LITERAL
IN = sre.IN
MAXREPEAT = sre_parse.MAXREPEAT

# sre_parse AST node: (opcode, value)
# Value varies by opcode (int for LITERAL/AT, list for IN, tuple for REPEAT/SUBPATTERN/BRANCH, None for ANY)
# so `Any` is unavoidable here — this is a tagged union that Python's type system cannot express structurally
_Node: TypeAlias = tuple[int, Any]

# Specific value shapes for opcodes that carry structured data
_RepeatValue: TypeAlias = tuple[int, int, list[_Node]]
_SubpatternValue: TypeAlias = tuple[int | None, int, int, list[_Node]]
_BranchValue: TypeAlias = tuple[None, list[list[_Node]]]


@lru_cache
def update_quantifier(pattern: str, min_length: int | None, max_length: int | None) -> str:
    """Update the quantifier of a regular expression based on given min and max lengths."""
    if not pattern or (min_length in (None, 0) and max_length is None):
        return pattern

    try:
        parsed = sre_parse.parse(pattern)
        result = _transform(list(parsed), min_length, max_length)
        if result is None:
            return pattern
        global_flags = parsed.state.flags & ~_DEFAULT_FLAGS
        updated = _serialize(result, global_flags=global_flags)
        try:
            re.compile(updated)
        except re.error:
            return pattern
        return updated
    except (re.error, InternalError):
        # Invalid pattern or unsupported opcode — return unchanged
        return pattern


_REGEX_META = set(r"\.^$*+?{[|()")


def _serialize(nodes: list[_Node], *, global_flags: int = 0) -> str:
    """Serialize sre_parse AST back to regex string."""
    parts = []
    multi = len(nodes) > 1
    for op, value in nodes:
        s = _serialize_node((op, value))
        # BRANCH must be wrapped when concatenated with other nodes,
        # otherwise | extends to the end of the enclosing group
        if op == sre.BRANCH and multi:
            s = f"(?:{s})"
        parts.append(s)
    body = "".join(parts)
    if global_flags:
        prefix = _serialize_flags(global_flags, 0)
        return f"(?{prefix}){body}"
    return body


def _serialize_node(node: _Node) -> str:
    """Serialize a single AST node."""
    op, value = node
    match op, value:
        case sre.LITERAL, int():
            return _serialize_literal(value)
        case sre.NOT_LITERAL, int():
            return f"[^{_serialize_literal_in_class(value)}]"
        case sre.ANY, _:
            return "."
        case sre.AT, int():
            return _serialize_anchor(value)
        case sre.IN, list():
            return _serialize_in(value)
        case sre.BRANCH, tuple():
            return _serialize_branch(value)
        case sre.SUBPATTERN, tuple():
            return _serialize_subpattern(value)
        case op, tuple() if op in REPEATS:
            return _serialize_repeat(op, value)
        case sre.ASSERT, tuple():
            direction, subpattern = value
            inner = _serialize(list(subpattern))
            return f"(?<={inner})" if direction == -1 else f"(?={inner})"
        case sre.ASSERT_NOT, tuple():
            direction, subpattern = value
            inner = _serialize(list(subpattern))
            return f"(?<!{inner})" if direction == -1 else f"(?!{inner})"
        case sre.GROUPREF, int():
            return f"\\{value}"
        case sre.GROUPREF_EXISTS, tuple():
            group_id, yes_pattern, no_pattern = value
            yes = _serialize(list(yes_pattern))
            no = _serialize(list(no_pattern)) if no_pattern else None
            return f"(?({group_id}){yes}|{no})" if no is not None else f"(?({group_id}){yes})"
        case _ if op == getattr(sre, "ATOMIC_GROUP", None):
            return f"(?>{_serialize(list(value))})"
        case _:
            raise InternalError(f"Unsupported sre opcode: {op}")


def _serialize_literal(charcode: int) -> str:
    ch = chr(charcode)
    if ch in _REGEX_META:
        return "\\" + ch
    if not ch.isprintable():
        if charcode <= 0xFF:
            return f"\\x{charcode:02x}"
        if charcode <= 0xFFFF:
            return f"\\u{charcode:04x}"
        return f"\\U{charcode:08x}"
    return ch


def _serialize_anchor(anchor_type: int) -> str:
    match anchor_type:
        case sre.AT_BEGINNING | sre.AT_BEGINNING_STRING:
            return "^"
        case sre.AT_END | sre.AT_END_STRING:
            return "$"
        case sre.AT_BOUNDARY:
            return "\\b"
        case sre.AT_NON_BOUNDARY:
            return "\\B"
        case _:
            return "\\b"


def _serialize_in(items: list[_Node]) -> str:
    match items:
        case [(sre.NEGATE, _), *rest]:
            inner = "".join(_serialize_in_item(item) for item in rest)
            return f"[^{inner}]"
        case [(sre.CATEGORY, val)]:
            return _serialize_category(val)
        case rest:
            inner = "".join(_serialize_in_item(item) for item in rest)
            return f"[{inner}]"


def _serialize_in_item(node: _Node) -> str:
    op, value = node
    match op, value:
        case sre.LITERAL, int():
            return _serialize_literal_in_class(value)
        case sre.RANGE, (int() as lo, int() as hi):
            return f"{_serialize_literal_in_class(lo)}-{_serialize_literal_in_class(hi)}"
        case sre.CATEGORY, int():
            return _serialize_category(value)
        case _:
            return ""


_CLASS_META = set(r"\]^[-")


def _serialize_literal_in_class(charcode: int) -> str:
    ch = chr(charcode)
    if ch in _CLASS_META:
        return "\\" + ch
    if not ch.isprintable():
        if charcode <= 0xFF:
            return f"\\x{charcode:02x}"
        if charcode <= 0xFFFF:
            return f"\\u{charcode:04x}"
        return f"\\U{charcode:08x}"
    return ch


def _serialize_category(cat: int) -> str:
    match cat:
        case sre.CATEGORY_DIGIT:
            return "\\d"
        case sre.CATEGORY_NOT_DIGIT:
            return "\\D"
        case sre.CATEGORY_SPACE:
            return "\\s"
        case sre.CATEGORY_NOT_SPACE:
            return "\\S"
        case sre.CATEGORY_WORD:
            return "\\w"
        case sre.CATEGORY_NOT_WORD:
            return "\\W"
        case _:
            return ""


def _serialize_repeat(op: int, value: _RepeatValue) -> str:
    min_r, max_r, subpattern = value
    inner = _serialize_repeat_inner(subpattern)
    quantifier = _build_quantifier(min_r, max_r)
    match op:
        case sre.MIN_REPEAT:
            suffix = "?"
        case _ if op == getattr(sre, "POSSESSIVE_REPEAT", None):
            suffix = "+"
        case _:
            suffix = ""
    return inner + quantifier + suffix


def _serialize_repeat_inner(subpattern: list[_Node]) -> str:
    """Serialize the inner part of a repeat, wrapping in parens only when needed."""
    # sre_parse returns SubPattern objects (not plain lists) which don't
    # match sequence patterns in match/case — convert to list first.
    items = list(subpattern)
    match items:
        case [(sre.SUBPATTERN, value)]:
            return _serialize_subpattern(value)
        case [(op, _)] if op in (LITERAL, NOT_LITERAL, IN, sre.ANY, sre.CATEGORY):
            # Single atomic node — already a valid quantifier target, no group needed
            return _serialize_node(items[0])
        case _:
            return "(?:" + _serialize(items) + ")"


def _serialize_subpattern(value: _SubpatternValue) -> str:
    group_id, add_flags, del_flags, pattern = value
    inner = _serialize(pattern)
    flags = _serialize_flags(add_flags, del_flags)
    if group_id is None or group_id == 0:
        return f"(?{flags}:{inner})" if flags else f"(?:{inner})"
    if flags:
        # Capturing group with flags — wrap: (?flags:(inner))
        return f"(?{flags}:({inner}))"
    return f"({inner})"


# Flag bit -> letter mapping for inline flag serialization
_FLAG_LETTERS: tuple[tuple[int, str], ...] = (
    (sre.SRE_FLAG_IGNORECASE, "i"),
    (sre.SRE_FLAG_LOCALE, "L"),
    (sre.SRE_FLAG_MULTILINE, "m"),
    (sre.SRE_FLAG_DOTALL, "s"),
    (sre.SRE_FLAG_UNICODE, "u"),
    (sre.SRE_FLAG_VERBOSE, "x"),
)
if hasattr(sre, "SRE_FLAG_ASCII"):
    _FLAG_LETTERS += ((sre.SRE_FLAG_ASCII, "a"),)

# Default flags set by sre_parse (Unicode mode)
_DEFAULT_FLAGS = sre.SRE_FLAG_UNICODE


def _serialize_flags(add_flags: int, del_flags: int) -> str:
    """Serialize inline flags like 'i', 'im', 'i-s'."""
    add = "".join(ch for flag, ch in _FLAG_LETTERS if add_flags & flag)
    sub = "".join(ch for flag, ch in _FLAG_LETTERS if del_flags & flag)
    if sub:
        return f"{add}-{sub}"
    return add


def _serialize_branch(value: _BranchValue) -> str:
    _, alternatives = value
    return "|".join(_serialize(alt) for alt in alternatives)


_AT_BEGINNING: _Node = (sre.AT, sre.AT_BEGINNING)
_AT_END: _Node = (sre.AT, sre.AT_END)


def _transform(parsed: list[_Node], min_length: int | None, max_length: int | None) -> list[_Node] | None:
    """Top-level transformer. Dispatches by pattern structure."""
    nodes = list(parsed)
    match _classify_structure(nodes):
        # For cases without full anchoring: JSON Schema `pattern` is a substring match,
        # so `{1,50}` alone doesn't reject strings longer than 50 chars. Add the missing
        # anchor(s) whenever max_length is being encoded into the quantifier.
        case ("single", content):
            result = _transform_node(content, min_length, max_length)
            if result is None:
                return None
            if max_length is not None:
                return [_AT_BEGINNING, result, _AT_END]
            return [result]
        case ("leading_anchor", anchor, content):
            result = _transform_node(content, min_length, max_length)
            if result is None:
                return None
            if max_length is not None:
                return [anchor, result, _AT_END]
            return [anchor, result]
        case ("trailing_anchor", content, anchor):
            result = _transform_node(content, min_length, max_length)
            if result is None:
                return None
            if max_length is not None:
                return [_AT_BEGINNING, result, anchor]
            return [result, anchor]

        case ("both_anchors", leading, content, trailing):
            return _transform_anchored_single(leading, content, trailing, min_length, max_length)

        case ("anchored_multi", leading, parts, trailing):
            return _transform_anchored_multi(leading, parts, trailing, min_length, max_length)

        case _:
            return None


# Return type for _classify_structure — Literal tags let mypy narrow captures in match/case
_Structure: TypeAlias = (
    tuple[Literal["single"], _Node]
    | tuple[Literal["leading_anchor"], _Node, _Node]
    | tuple[Literal["trailing_anchor"], _Node, _Node]
    | tuple[Literal["both_anchors"], _Node, _Node, _Node]
    | tuple[Literal["anchored_multi"], _Node, list[_Node], _Node]
    | tuple[Literal["unknown"]]
)


def _classify_structure(nodes: list[_Node]) -> _Structure:
    """Classify pattern structure for dispatch."""
    _CONTENT_OPS = (LITERAL, NOT_LITERAL, IN, sre.ANY, *REPEATS)
    match nodes:
        case [content]:
            return ("single", content)
        case [(sre.AT, _) as anchor, content]:
            return ("leading_anchor", anchor, content)
        case [content, (sre.AT, _) as anchor]:
            return ("trailing_anchor", content, anchor)
        case [(sre.AT, _) as leading, content, (sre.AT, _) as trailing]:
            return ("both_anchors", leading, content, trailing)
        case [(sre.AT, _) as leading, *parts, (sre.AT, _) as trailing] if all(op in _CONTENT_OPS for op, _ in parts):
            return ("anchored_multi", leading, parts, trailing)
        case _:
            return ("unknown",)


def _transform_node(node: _Node, min_l: int | None, max_l: int | None) -> _Node | None:
    """Transform a single content node."""
    op, value = node
    if op in REPEATS:
        return _transform_repeat(op, value, min_l, max_l)
    if op in (LITERAL, NOT_LITERAL, IN) and max_l != 0:
        return _wrap_as_repeat(node, min_l, max_l)
    if op == sre.ANY:
        return _transform_repeat(sre.MAX_REPEAT, (1, 1, [(sre.ANY, None)]), min_l, max_l)
    return None


def _transform_repeat(op: int, value: _RepeatValue, min_l: int | None, max_l: int | None) -> _Node | None:
    """Core transform: merge length constraints into repeat bounds."""
    min_repeat, max_repeat, subpattern = value
    inner_length = _calculate_min_repetition_length(subpattern)

    if inner_length == 0:
        if min_l is not None and min_l > 0:
            return None
        return (sre.MAX_REPEAT, (0, 0, subpattern))

    if max_l is not None and 0 < max_l < inner_length:
        return None

    # Convert length constraints to repetition counts
    ext_min = None
    ext_max = None
    if min_l is not None:
        ext_min = -(-min_l // inner_length)  # ceil division
    if max_l is not None:
        ext_max = max_l // inner_length  # floor division

    # Merge with existing bounds
    final_min = min_repeat
    if ext_min is not None:
        final_min = max(min_repeat, ext_min)

    final_max = max_repeat
    if ext_max is not None:
        final_max = ext_max if max_repeat == MAXREPEAT else min(max_repeat, ext_max)

    if final_min > final_max:
        return None

    # Bounds unchanged + finite max + variable-length inner content means the
    # outer repetition count alone can't encode maxLength (inner quantifiers can
    # still expand the string further). Signal no-op so the caller keeps the
    # length constraints on the schema instead of silently discarding them.
    if (
        final_min == min_repeat
        and final_max == max_repeat
        and max_repeat != MAXREPEAT
        and _has_variable_length(list(subpattern))
    ):
        return None

    return (sre.MAX_REPEAT, (final_min, final_max, subpattern))


def _wrap_as_repeat(node: _Node, min_l: int | None, max_l: int | None) -> _Node:
    """Wrap a single-char node as a repeat."""
    min_r = 1 if min_l is None else max(min_l, 1)
    max_r = max_l if max_l is not None else MAXREPEAT
    return (sre.MAX_REPEAT, (min_r, max_r, [node]))


def _transform_anchored_single(
    leading: _Node,
    content: _Node,
    trailing: _Node,
    min_l: int | None,
    max_l: int | None,
) -> list[_Node] | None:
    r"""^content$ with canonicalization of [\w\W] -> . before transform."""
    op, value = content

    # Canonicalize match-anything patterns
    if op == sre.IN and _matches_anything(value):
        content = (sre.ANY, None)
    elif op in REPEATS and len(value[2]) == 1 and value[2][0][0] == sre.IN and _matches_anything(value[2][0][1]):
        content = (op, (value[0], value[1], [(sre.ANY, None)]))

    op, value = content
    if op == LITERAL and ((min_l is not None and min_l > 1) or (max_l is not None and max_l < 1)):
        return None

    result = _transform_node(content, min_l, max_l)
    if result is None:
        return None
    return [leading, result, trailing]


def _transform_anchored_multi(
    leading: _Node,
    parts: list[_Node],
    trailing: _Node,
    min_l: int | None,
    max_l: int | None,
) -> list[_Node] | None:
    """^part1 part2 ... partN$ — multiple quantified/fixed parts."""
    fixed_length = 0
    quantifier_bounds = []
    repetition_lengths = []
    quantified_indices = []

    for idx, (op, value) in enumerate(parts):
        if op in (LITERAL, NOT_LITERAL, IN, sre.ANY):
            fixed_length += 1
        elif op in REPEATS:
            min_repeat, max_repeat, subpattern = value
            quantifier_bounds.append((min_repeat, max_repeat))
            repetition_lengths.append(_calculate_min_repetition_length(subpattern))
            quantified_indices.append(idx)

    adj_min = None if min_l is None else min_l - fixed_length
    adj_max = None if max_l is None else max_l - fixed_length

    if (adj_min is not None and adj_min < 0) or (adj_max is not None and adj_max < 0):
        return None

    if not quantifier_bounds:
        return None

    distribution = _distribute_length_constraints(quantifier_bounds, repetition_lengths, adj_min, adj_max)
    if not distribution:
        return None

    # An unchanged finite outer bound with variable-length inner content cannot
    # enforce maxLength — each outer tick may exceed rep_len chars.
    if adj_max is not None:
        for dist_idx, (_, new_max) in enumerate(distribution):
            if new_max != MAXREPEAT:
                part_idx = quantified_indices[dist_idx]
                _, (_, orig_max, inner_subpattern) = parts[part_idx]
                if orig_max != MAXREPEAT and new_max == orig_max and _has_variable_length(list(inner_subpattern)):
                    return None

    # Apply distribution directly to AST nodes
    new_parts = list(parts)
    for dist_idx, part_idx in enumerate(quantified_indices):
        op, value = parts[part_idx]
        new_min, new_max = distribution[dist_idx]
        _, _, subpattern = value
        new_parts[part_idx] = (sre.MAX_REPEAT, (new_min, new_max, subpattern))

    return [leading] + new_parts + [trailing]


def _matches_anything(value: list[_Node]) -> bool:
    """Check if the given pattern is equivalent to '.' (match any character)."""
    return value in (
        [(sre.CATEGORY, sre.CATEGORY_WORD), (sre.CATEGORY, sre.CATEGORY_NOT_WORD)],
        [(sre.CATEGORY, sre.CATEGORY_SPACE), (sre.CATEGORY, sre.CATEGORY_NOT_SPACE)],
        [(sre.CATEGORY, sre.CATEGORY_DIGIT), (sre.CATEGORY, sre.CATEGORY_NOT_DIGIT)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_WORD), (sre.CATEGORY, sre.CATEGORY_WORD)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_SPACE), (sre.CATEGORY, sre.CATEGORY_SPACE)],
        [(sre.CATEGORY, sre.CATEGORY_NOT_DIGIT), (sre.CATEGORY, sre.CATEGORY_DIGIT)],
    )


def _distribute_length_constraints(
    bounds: list[tuple[int, int]], repetition_lengths: list[int], min_length: int | None, max_length: int | None
) -> list[tuple[int, int]] | None:
    """Distribute length constraints among quantified pattern parts."""
    if min_length == max_length:
        assert min_length is not None
        return _distribute_exact_length(bounds, repetition_lengths, min_length)
    return _distribute_length_range(bounds, repetition_lengths, min_length, max_length)


def _distribute_exact_length(
    bounds: list[tuple[int, int]], repetition_lengths: list[int], target: int
) -> list[tuple[int, int]] | None:
    """Find exact repetition counts that sum to target length via dynamic programming."""
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


def _distribute_length_range(
    bounds: list[tuple[int, int]], repetition_lengths: list[int], min_length: int | None, max_length: int | None
) -> list[tuple[int, int]] | None:
    """Greedy single-pass distribution of min/max length budget across quantifiers."""
    result = []
    remaining_min = min_length or 0
    remaining_max = MAXREPEAT if max_length is None else max_length

    for (min_repeat, max_repeat), rep_len in zip(bounds, repetition_lengths, strict=True):
        if rep_len == 0:
            result.append((0, 0))
            continue

        if remaining_min > 0:
            part_min = min(max_repeat, max(min_repeat, -(-remaining_min // rep_len)))
        else:
            part_min = min_repeat

        if remaining_max < MAXREPEAT:
            part_max = min(max_repeat, remaining_max // rep_len) if rep_len > 0 else max_repeat
        else:
            part_max = max_repeat

        if part_min > part_max:
            return None

        result.append((part_min, part_max))

        remaining_min = max(0, remaining_min - part_min * rep_len)
        remaining_max -= part_max * rep_len if part_max != MAXREPEAT else 0

    if remaining_min > 0 or remaining_max < 0:
        return None

    return result


def _calculate_min_repetition_length(subpattern: list[_Node]) -> int:
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
        elif op == sre.BRANCH:
            _, alternatives = value
            branch_min = min(_calculate_min_repetition_length(list(alt)) for alt in alternatives)
            total += branch_min
    return total


def _has_variable_length(nodes: list[_Node]) -> bool:
    """Return True if the nodes can produce strings of variable length (contain *, +, ?, {n,m}, or branches)."""
    for op, value in nodes:
        if op in REPEATS:
            min_r, max_r, inner = value
            if min_r != max_r:
                return True
            if _has_variable_length(list(inner)):
                return True
        elif op == sre.SUBPATTERN:
            _, _, _, inner = value
            if _has_variable_length(list(inner)):
                return True
        elif op == sre.BRANCH:
            return True
    return False


def _build_quantifier(minimum: int | None, maximum: int | None) -> str:
    """Construct a quantifier string based on min and max values."""
    if maximum == MAXREPEAT or maximum is None:
        return f"{{{minimum or 0},}}"
    if minimum == maximum:
        return f"{{{minimum}}}"
    return f"{{{minimum or 0},{maximum}}}"
