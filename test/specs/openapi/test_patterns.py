import re
import string
import sys
import warnings

import jsonschema_rs
import pytest
from flask import jsonify
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse  # type: ignore[no-redef]

import schemathesis
from schemathesis.core.errors import InternalError
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.specs.openapi.converter import update_pattern_in_schema
from schemathesis.specs.openapi.patterns import (
    _PARTIAL_SCRIPT_CLASSES,
    _UNICODE_PROPERTY_RAW_MAP,
    _serialize,
    is_valid_jsonschema_rs_regex,
    normalize_regex,
    pattern_length_bounds,
    pattern_requires_char_outside,
    pattern_requires_literal,
    update_quantifier,
)

SKIP_BEFORE_PY11 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Possessive repeat is only available in Python 3.11+"
)


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        # Single literal
        ("a", None, 3, "^a{1,3}$"),
        ("a", 3, 3, "^a{3}$"),
        ("a", 0, 3, "^a{1,3}$"),
        ("}?", 1, None, "}{1}"),
        # Simple quantifiers on a simple group
        (".*", None, 3, "^.{0,3}$"),
        (".*", 0, 3, "^.{0,3}$"),
        (".*", 1, None, ".{1,}"),
        (".*", 1, 3, "^.{1,3}$"),
        (".+", None, 3, "^.{1,3}$"),
        (".+", 1, None, ".{1,}"),
        (".+", 1, 3, "^.{1,3}$"),
        (".+", 0, 3, "^.{1,3}$"),
        (".?", 0, 3, "^.{0,1}$"),
        (".*?", 0, 3, "^.{0,3}$"),
        (".+?", 0, 3, "^.{1,3}$"),
        # Complex quantifiers on a simple group
        (".{1,5}", None, 3, "^.{1,3}$"),
        (".{0,3}", 1, None, ".{1,3}"),
        (".{2,}", 1, 3, "^.{2,3}$"),
        (".{1,5}?", None, 3, "^.{1,3}$"),
        (".{0,3}?", 1, None, ".{1,3}"),
        (".{2,}?", 1, 3, "^.{2,3}$"),
        pytest.param(".{1,5}+", None, 3, "^.{1,3}$", marks=SKIP_BEFORE_PY11),
        pytest.param(".{0,3}+", 1, None, ".{1,3}", marks=SKIP_BEFORE_PY11),
        pytest.param(".{2,}+", 1, 3, "^.{2,3}$", marks=SKIP_BEFORE_PY11),
        # Group without quantifier
        ("[a-z]", None, 5, "^[a-z]{1,5}$"),
        ("[a-z]", 3, None, "[a-z]{3,}"),
        ("[a-z]", 3, 5, "^[a-z]{3,5}$"),
        ("[a-z]", 1, 5, "^[a-z]{1,5}$"),
        ("a|b", 1, 5, "^[ab]{1,5}$"),
        # A more complex group with `*` quantifier
        ("[a-z]*", None, 5, "^[a-z]{0,5}$"),
        ("[a-z]*", 3, None, "[a-z]{3,}"),
        ("[a-z]*", 3, 5, "^[a-z]{3,5}$"),
        ("[a-z]*", 1, 5, "^[a-z]{1,5}$"),
        # With anchors
        ("^[a-z]*", None, 5, "^[a-z]{0,5}$"),
        ("^[a-z]*", 3, 5, "^[a-z]{3,5}$"),
        ("^[a-z]+", 0, 5, "^[a-z]{1,5}$"),
        ("^[a-z]*$", None, 5, "^[a-z]{0,5}$"),
        ("^[a-z]*$", 3, 5, "^[a-z]{3,5}$"),
        ("^[a-z]+$", 0, 5, "^[a-z]{1,5}$"),
        ("^.+$", 0, 5, "^.{1,5}$"),
        ("^.{0,1}$", 0, 5, "^.{0,1}$"),
        ("^.$", 0, 5, "^.{1}$"),
        # Fully anchored single-char content matches exactly one character, so the length
        # budget may only narrow it - widening would admit strings the pattern rejects.
        ("^[a-z]$", None, 2, "^[a-z]{1}$"),
        ("^[a-z]$", 1, 3, "^[a-z]{1}$"),
        ("^[a-z]$", 2, 5, "^[a-z]$"),
        (r"^\S$", None, 2, r"^\S{1}$"),
        ("^[^x]$", 1, 4, "^[^x]{1}$"),
        # Unanchored `.` is a substring requirement like any other single-char node.
        (".", None, 2, "^.{1,2}$"),
        (".", 2, 5, "^.{2,5}$"),
        (".", 2, None, ".{2,}"),
        ("[a-z]*$", None, 5, "^[a-z]{0,5}$"),
        ("[a-z]*$", 3, 5, "^[a-z]{3,5}$"),
        ("[a-z]+$", 0, 5, "^[a-z]{1,5}$"),
        (r"\d*", 1, None, r"\d{1,}"),
        (r"0\A", 1, None, r"0{1,}^"),
        # Noop
        ("abc*def*", 1, 3, "abc*def*"),
        ("[bc]*[de]*", 1, 3, "[bc]*[de]*"),
        ("[bc]3", 1, 3, "[bc]3"),
        ("b{30,35}", 1, 3, "b{30,35}"),
        ("b{1,3}", 10, None, "b{1,3}"),
        ("b", 0, 0, "b"),
        ("b$", None, None, "b$"),
        ("b$", 0, None, "b$"),
        ("}?", 0, None, "}?"),
        # Literal length is outside of the quantifiers range
        ("^0$", 2, 2, "^0$"),
        ("^0$", 2, None, "^0$"),
        ("^0$", 0, 0, "^0$"),
        # More complex patterns
        # Fixed parts with single quantifier
        ("^abc[0-9]*$", None, 5, "^abc[0-9]{0,2}$"),
        ("^-[a-z]{1,10}-$", None, 4, "^-[a-z]{1,2}-$"),
        # Multiple quantifiers
        (r"^[a-z]{2,4}-\d{4,15}$", 7, 7, r"^[a-z]{2}-\d{4}$"),
        (r"^[a-z]{2,4}-\d{4,15}$", 20, 20, r"^[a-z]{4}-\d{15}$"),
        # Complex patterns with multiple parts
        ("^[A-Z]{1,3}-[0-9]{2,4}-[a-z]{1,5}$", 8, 8, "^[A-Z]{1}-[0-9]{2}-[a-z]{3}$"),
        (r"^\w{2,4}:\d{3,5}:[A-F]{1,2}$", 10, 10, r"^\w{2}:\d{4}:[A-F]{2}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 7, 7, r"^[a-zA-Z0-9]{2}-\d{4}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 8, 8, r"^[a-zA-Z0-9]{2}-\d{5}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 19, 19, r"^[a-zA-Z0-9]{3}-\d{15}$"),
        (r"^([a-zA-Z0-9]){2,4}-(\d){4,15}$", 19, 19, r"^([a-zA-Z0-9]){3}-(\d){15}$"),
        (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 50, 50, r"^[a-zA-Z0-9]{2,4}-\d{4,15}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 1, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 1, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", None, None, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 0, None, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", None, 5, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, None, r"^abcd[a-zA-Z0-9]{2,4}$"),
        (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 10, r"^abcd[a-zA-Z0-9]{2,4}$"),
        # When greedy would collapse the variable-inner suffix to `{0}`, the
        # distributor falls back to a balanced split that gives both slots room
        # — each slot's max gets the budget minus what other slots are required to
        # consume, so the rewrite admits any skewed distribution the original allows.
        (
            r"^[a-zA-Z0-9]+([-a-zA-Z0-9]?[a-zA-Z0-9])*$",
            5,
            64,
            r"^[a-zA-Z0-9]{5,64}([\-a-zA-Z0-9]{0,1}[a-zA-Z0-9]){0,31}$",
        ),
        (r"^\+[0-9]{5,}$", 6, 6, r"^\+[0-9]{5}$"),
        (r"^abcd$", 50, 50, r"^abcd$"),
        # Edge cases
        # Exact-length DP picks a non-zero shape when one fits, avoiding an
        # unnecessary `{0}` collapse on either optional slot.
        ("^[a-z]*-[0-9]*$", 3, 3, "^[a-z]{1}-[0-9]{1}$"),
        (r"^[+][\s0-9()-]+$", 1, 20, r"^\+[\s0-9()\-]{1,19}$"),
        (r"^[\+][\s0-9()-]+$", 1, 20, r"^\+[\s0-9()\-]{1,19}$"),
        # Multiple fixed parts
        ("^abc[0-9]{1,3}def[a-z]{2,5}ghi$", 12, 12, "^abc[0-9]{1}def[a-z]{2}ghi$"),
        # Others
        ("^(((?:DB|BR)[-a-zA-Z0-9_]+),?){1,}$", None, 6000, r"^(((?:DB|BR)[\-a-zA-Z0-9_]{1,}),{0,1}){1,2000}$"),
        # Optional `\*?` is preserved (collapsing it to `{0}` would reject "geo:abc*"),
        # while `\w*` still tightens to use the remaining length budget.
        (r"^geo:\w*\*?$", 5, 200, r"^geo:\w{1,195}\*{0,1}$"),
        (r"^[\w\W]$", 1, 3, r"^.{1}$"),
        (r"^[\w\W]+$", 1, 3, r"^.{1,3}$"),
        (r"^[\w\W]*$", 1, 3, r"^.{1,3}$"),
        (r"^[\w\W]?$", 1, 3, r"^.{1}$"),
        (r"^[\w\W]{2,}$", 1, 3, r"^.{2,3}$"),
        (r"^[\W\w]$", 1, 3, r"^.{1}$"),
        (r"^[\W\w]+$", 1, 3, r"^.{1,3}$"),
        (r"^[\W\w]*$", 1, 3, r"^.{1,3}$"),
        (r"^[\W\w]?$", 1, 3, r"^.{1}$"),
        (r"^[\W\w]{2,}$", 1, 3, r"^.{2,3}$"),
        # Variable-length inner: outer count alone cannot encode maxLength (each
        # tick may be arbitrarily long), so we pin the variable slot and tighten
        # the leading slot for minLength only.
        # At an exact length the leading slot spends the whole budget, so the trailing repeat drops out.
        (r"^prefix[|]+(?:,prefix[|]+)*$", 4000, 4000, r"^prefix\|{3994}(?:,prefix\|{1,}){0}$"),
        (r"^bar\.spam\.[^,]+(?:,bar\.spam\.[^,]+)*$", 10, 10, r"^bar\.spam\.[^,]{1}(?:,bar\.spam\.[^,]{1,}){0}$"),
        # Optional finite group `()?` is preserved while `8+` tightens to use the budget.
        (r"^\008+()?$", None, 2, r"^\x008{1}(){0,1}$"),
        (r"^\008+()?$", 2, None, r"^\x008{1,}(){0,1}$"),
        (r"^000(000)?$", 4, 5, r"^000(000)?$"),
        ("(abc)+", 1, 10, "^(abc){1,3}$"),
        ("(hello){2,5}", None, 12, "^(hello){2}$"),
        ("(abcd)*", 3, 7, "^(abcd){1}$"),
        ("^()?$", 4, 5, "^()?$"),
        # Global inline flags
        (r"(?i).*", 1, 5, r"(?i)^.{1,5}$"),
        (r"(?i)[a-z]+", 2, 8, r"(?i)^[a-z]{2,8}$"),
        (r"(?im)[a-z]+", 1, 5, r"(?im)^[a-z]{1,5}$"),
        (r"(?s).+", 1, 3, r"(?s)^.{1,3}$"),
        # \b and \B anchors
        (r"\b[a-z]+\b", 2, 5, r"\b[a-z]{2,5}\b"),
        (r"\B\d+\B", 1, 3, r"\B\d{1,3}\B"),
        # Non-printable literal (U+200B, 0x100–0xFFFF range) — single-char class collapses to LITERAL
        (f"[{chr(0x200B)}]+", 2, 5, "^\\u200b{2,5}$"),
        # Non-printable range in character class (stays as IN with RANGE)
        (f"[{chr(0x200B)}-{chr(0x200D)}]+", 2, 5, "^[\\u200b-\\u200d]{2,5}$"),
        # Subpatterns with inline flags
        ("(?i:[a-z])+", 2, 5, "^(?i:[a-z]){2,5}$"),
        ("(?im:[a-z])+", 1, 4, "^(?im:[a-z]){1,4}$"),
        ("(?-i:[A-Z])+", 2, 4, "^(?-i:[A-Z]){2,4}$"),
        ("(?i-m:[a-z])+", 1, 3, "^(?i-m:[a-z]){1,3}$"),
        # Empty capturing group — zero-length inner
        (r"^()+$", 0, 5, "^(){0}$"),
        (r"^()+$", 1, 5, r"^()+$"),
        # Multi-char quantified inner in groups
        (r"(\d{3})+", 6, 9, r"^(\d{3}){2,3}$"),
        (r"(\d{3})+", 3, 3, r"^(\d{3}){1}$"),
        (r"([A-Z]{2})+", 4, 10, r"^([A-Z]{2}){2,5}$"),
        (r"([A-Z]\d)+", 4, 8, r"^([A-Z]\d){2,4}$"),
        (r"(\d{2}){1,5}", 4, 8, r"^(\d{2}){2,4}$"),
        (r"(\d{2}){3,7}", 8, 12, r"^(\d{2}){4,6}$"),
        (r"(\d{3})+", 9, None, r"(\d{3}){3,}"),
        (r"(\d{3})+", None, 9, r"^(\d{3}){1,3}$"),
        (r"(?:\d{3})+", 6, 9, r"^(?:\d{3}){2,3}$"),
        (r"(\d{3})+", 1, 2, r"(\d{3})+"),
        (r"(\d{3})+", 7, 7, r"(\d{3})+"),
        (r"(\d{3})+", 0, 0, r"(\d{3})+"),
        (r"(a|bb)+", 4, 8, r"^(a|bb){4,8}$"),
        # Anchored multi-part with multi-char groups
        (r"^(abc)+(def)+$", 6, 6, r"^(abc){1}(def){1}$"),
        (r"^(abc)+(def)+$", 9, 9, r"^(abc){1}(def){2}$"),
        (r"^(ab)+(cd)+(ef)+$", 6, 6, r"^(ab){1}(cd){1}(ef){1}$"),
        (r"^(ab)+(cd)+(ef)+$", 10, 10, r"^(ab){1}(cd){1}(ef){3}$"),
        (r"^(\d{3})+(\w{2})+$", 10, 10, r"^(\d{3}){2}(\w{2}){2}$"),
        (r"^(abc)+\d+$", 4, 10, r"^(abc){2,3}\d{1}$"),
        (r"^(abc)+(\d)+$", 7, 7, r"^(abc){1}(\d){4}$"),
        (r"^abc(\d{3})+$", 6, 12, r"^abc(\d{3}){1,3}$"),
        # Patterns containing lookahead / lookbehind assertions inside quantified groups
        (r"^([a-z]+(?<!\s))+$", 1, 5, r"^([a-z]{1,}(?<!\s)){1,5}$"),
        (r"^([a-z]+(?!\s))+$", 1, 5, r"^([a-z]{1,}(?!\s)){1,5}$"),
        (r"^([a-z]+(?<=\s))+$", 1, 5, r"^([a-z]{1,}(?<=\s)){1,5}$"),
        (r"^([a-z]+(?=\s))+$", 1, 5, r"^([a-z]{1,}(?=\s)){1,5}$"),
        # Branch alternation with finite per-tick max (1 or 2 chars) — outer count
        # tightens to keep total length within bounds.
        (r"^[a-z0-9]([a-z0-9]|-[a-z0-9])*$", 1, 100, r"^[a-z0-9]([a-z0-9]|-[a-z0-9]){0,49}$"),
        (r"^(foo|bar)+$", 3, 12, r"^(foo|bar){1,4}$"),
        # Outer bound already finite and unchanged; inner content is variable-length
        # — maxLength cannot be encoded through the outer repetition count alone.
        (r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", 1, 63, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"),
        # Optional group with variable inner: minLength absorbed but maxLength unrepresentable.
        (r"^([a-z][a-z]*)?$", 1, 5, r"^([a-z][a-z]*)?$"),
        # Multi-part anchored pattern with an optional variable-length middle group:
        # the variable-inner middle pin keeps `(...){0,2}` (collapsing it to `{0}`
        # would reject "Doe-Smith"), while the trailing `\.?` is also pinned.
        # maxLength cannot be enforced (middle slot can grow unboundedly), but the
        # leading slot still tightens for minLength.
        (
            r"^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\.?$",
            1,
            30,
            r"^[a-zA-Z]{1,30}([ '\-][a-zA-Z]{1,}){0,2}\.{0,1}$",
        ),
        # Required-only siblings whose combined max can't reach the target minLength —
        # both greedy and balanced bail and the original schema is kept.
        (r"^[a-z]{1,3}_[a-z]{1,3}$", 10, 12, r"^[a-z]{1,3}_[a-z]{1,3}$"),
        # Nested unbounded inner (`([a-z]+)+`) — the per-tick max is unbounded so
        # the slot is pinned and only the outer count is preserved.
        (r"^([a-z]+)+$", 1, 100, r"^([a-z]{1,}){1,100}$"),
        # Bounded outer with unbounded nested inner — the outer count cannot bound
        # the slot, the slot is pinned, and the rewrite stays at the original.
        (r"^([a-z]+){2,5}$", 1, 50, r"^([a-z]+){2,5}$"),
        # Multi-quantifier with one zero-contributing slot (empty group). Both the
        # greedy and balanced distributors recognize the slot as silent and refuse
        # to use it for the length budget.
        (r"^a+()+$", 2, 5, r"^a+()+$"),
        # Pinned slot with unbounded inner alongside a finite sibling whose max can't
        # absorb the remaining min-length budget — both distributors must bail rather
        # than crash, and the original pattern is kept.
        (r"^(.|a+){1,3}\d{1,3}$", 100, 100, r"^(.|a+){1,3}\d{1,3}$"),
        # A possessive part keeps everything it took, so any rewritten count admits strings the
        # original rejects - `0++0` matches nothing at all, while `0{1,3}0` matches "00".
        pytest.param("0++0", None, 4, "0++0", marks=SKIP_BEFORE_PY11),
        pytest.param("a{1,3}+a", None, 4, "a{1,3}+a", marks=SKIP_BEFORE_PY11),
        pytest.param("(?:ab)++ab", None, 4, "(?:ab)++ab", marks=SKIP_BEFORE_PY11),
        pytest.param("a+b++c", 1, 4, "a+b++c", marks=SKIP_BEFORE_PY11),
        # Anchored only at the start, with more than one part: the budget still lands, and encoding
        # a maximum closes the pattern so longer strings stop matching.
        ("^[a-zA-Z][a-zA-Z0-9_]*", 63, 63, "^[a-zA-Z][a-zA-Z0-9_]{62}$"),
        ("^[a-zA-Z][a-zA-Z0-9_]*", 1, 63, "^[a-zA-Z][a-zA-Z0-9_]{0,62}$"),
        ("^[a-z][0-9]+", 3, 6, "^[a-z][0-9]{2,5}$"),
        ("^foo[a-z]+", 4, 8, "^foo[a-z]{1,5}$"),
        ("^[a-z][0-9]+", 5, None, "^[a-z][0-9]{4,}"),
        # A group is a part like any other, and its own repeat is where its share of the budget goes.
        ("arn:([a-z0-9-]+):forecast:.*:.*:.+", 20, 40, r"^arn:([a-z0-9\-]{2,23}):forecast:.{0,22}:.{0,22}:.{2,23}$"),
        ("^prefix-([a-z]+)-[0-9]+$", 12, 24, "^prefix-([a-z]{2,15})-[0-9]{2,15}$"),
        ("^([a-z]+)-([0-9]+)$", 6, 12, "^([a-z]{3,10})-([0-9]{2,10})$"),
        # A group wrapping the whole pattern is transparent to the budget - the quantifier inside it still tightens.
        ("^([a-zA-Z0-9]*)$", 5, 5, "^([a-zA-Z0-9]{5})$"),
        ("^([a-zA-Z0-9]*)$", 1, 10, "^([a-zA-Z0-9]{1,10})$"),
        ("^([a-z]+)$", None, 7, "^([a-z]{1,7})$"),
        ("([a-z]*)", 4, 4, "^([a-z]{4})$"),
        ("^(?i:[a-z]*)$", 3, 3, "^(?i:[a-z]{3})$"),
        ("^(?i:([a-z]*))$", 2, 6, "^(?i:([a-z]{2,6}))$"),
        ("^((a*))$", 2, 2, "^((a{2}))$"),
        # A group holding anything but one quantifiable item has no single place for the budget to land.
        ("^(a|bb)$", 2, 2, "^(a|bb)$"),
        ("^([a-z][0-9]*)$", 3, 3, "^([a-z][0-9]*)$"),
    ],
)
def test_update_quantifier(pattern, min_length, max_length, expected):
    assert update_quantifier(pattern, min_length, max_length) == expected
    re.compile(expected)


@pytest.mark.parametrize(
    ("pattern", "length"),
    [
        (r"^[ \t]*[\x20-\x7E]+([ \t]+[\x20-\x7E]+)*[ \t]*$", 512),
        (r"^prefix[|]+(?:,prefix[|]+)*$", 4000),
        (r"^bar\.spam\.[^,]+(?:,bar\.spam\.[^,]+)*$", 10),
        (r"^[a-zA-Z0-9]+(-*[a-zA-Z0-9])*$", 25),
        (r"^([a-zA-ZÀ-ÖØ-öø-ɏ \t\n\r\f\v0-9_.:/=+\-@]*)$", 256),
        (r"^(?i:[a-z_]*)$", 128),
    ],
)
def test_update_quantifier_exact_length_is_bounded(pattern, length):
    # An open-ended repeat here leaves only zero-repetition matches at this length, which generation never finds.
    assert pattern_length_bounds(update_quantifier(pattern, length, length)) == (length, length)


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length"),
    [
        (r"^([a-zA-Z0-9]*)$", 5, 5),
        (r"^([a-zA-Z0-9_.-]+)$", 3, 12),
        (r"^(?i:[a-z]*)$", 4, 4),
        (r"^(?i:([a-z-]+))$", 2, 9),
        (r"^([a-zA-ZÀ-ÖØ-öø-ɏ \t\n\r\f\v0-9_.:/=+\-@]*)$", 256, 256),
    ],
)
@settings(max_examples=30, suppress_health_check=list(HealthCheck))
@given(data=st.data())
def test_group_rewrite_stays_within_the_original_language(pattern, min_length, max_length, data):
    rewritten = update_quantifier(pattern, min_length, max_length)
    assert rewritten != pattern
    value = data.draw(st.from_regex(rewritten, fullmatch=True, alphabet=st.characters(codec=None)))
    assert re.search(pattern, value), f"{value!r} does not match {pattern}"
    assert min_length <= len(value) <= max_length


def test_update_quantifier_keeps_repeat_matching_empty():
    # This repeat spends none of the length budget, so dropping it would lose the strings that use it.
    assert update_quantifier(r"^[a-z]+(b*)*$", 4, 4) == r"^[a-z]{4}(b{0,}){0,}$"


def test_update_quantifier_invalid_pattern():
    assert update_quantifier("*", 1, 3) == "*"


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        ("[a-z][a-z]*", 1, 5, "^[a-z][a-z]{0,4}$"),
        ("[a-z][0-9]+", 3, 6, "^[a-z][0-9]{2,5}$"),
        ("[a-z][0-9]*", 4, 4, "^[a-z][0-9]{3}$"),
        ("[a-z][a-z0-9]*", 4, 4, "^[a-z][a-z0-9]{3}$"),
        ("foo[a-z]+", 4, 8, "^foo[a-z]{1,5}$"),
        ("[a-z][0-9]+", 5, None, "[a-z][0-9]{4,}"),
        ("abc*def*", 1, 3, "abc*def*"),
        ("[bc]*[de]*", 1, 3, "[bc]*[de]*"),
        ("[bc]3", 1, 3, "[bc]3"),
    ],
)
def test_update_quantifier_unanchored_multi(pattern, min_length, max_length, expected):
    assert update_quantifier(pattern, min_length, max_length) == expected
    re.compile(expected)


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length"),
    [
        ("[A-Za-z][A-Za-z0-9_.-]*", 1, 255),
        ("[a-z0-9_][a-z0-9_-]+[a-z0-9_]", 3, 63),
        ("[a-z]+[0-9]+", 4, 8),
        ("[a-z][0-9]*", 4, 4),
    ],
)
def test_update_quantifier_unanchored_multi_enforces_min(pattern, min_length, max_length):
    # The rewrite must bake the lower bound so generation produces long-enough strings.
    rewritten = update_quantifier(pattern, min_length, max_length)
    assert rewritten != pattern
    re.compile(rewritten)
    assert pattern_length_bounds(rewritten)[0] >= min_length


@pytest.mark.parametrize(
    ("min_length", "max_length", "value"),
    [
        # Multi-slot rewrite must accept any distribution that satisfies the original
        # pattern within the length budget; per-slot caps from a balanced split would
        # reject valid lengths the original pattern allowed.
        (1, 128, "A" + "0" * 100),
        (1, 128, "A" + "0" * 127),
        (1, 128, "A" * 128),
    ],
)
def test_update_quantifier_admits_uneven_slot_distributions(min_length, max_length, value):
    rewritten = update_quantifier(r"^[a-zA-Z*]+[a-zA-Z0-9-]*$", min_length, max_length)
    assert re.match(rewritten, value), (rewritten, value)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "string", "pattern": r"^[a-z]+$", "minLength": 1, "maxLength": 10},
            {"type": "string", "pattern": r"^[a-z]{1,10}$"},
        ),
        # Unbounded `{1,}` survives the rewrite; `maxLength` must stay so length is still enforced.
        (
            {"type": "string", "pattern": r"^([a-z]+-){2,3}\d+$", "minLength": 1, "maxLength": 32},
            {"type": "string", "pattern": r"^([a-z]{1,}-){2,3}\d{1,28}$", "maxLength": 32},
        ),
        # Each slot's max is allocated as if it were the only one, so together they outrun the budget.
        (
            {"type": "string", "pattern": r"^0+0+$", "maxLength": 3},
            {"type": "string", "pattern": r"^0{1,2}0{1,2}$", "maxLength": 3},
        ),
        (
            {"type": "string", "pattern": r"^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\.?$", "minLength": 1, "maxLength": 30},
            {
                "type": "string",
                "pattern": r"^[a-zA-Z]{1,30}([ '\-][a-zA-Z]{1,}){0,2}\.{0,1}$",
                "maxLength": 30,
            },
        ),
    ],
)
def test_update_pattern_in_schema_keeps_unenforced_bounds(schema, expected):
    update_pattern_in_schema(schema)
    assert schema == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        # Translatable patterns
        (
            r"^[\p{L}]+([ '-][\p{L}]+){0,2}$",
            r"^[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+([ '-][a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+){0,2}$",
        ),
        (r"\p{L}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\p{N}+", r"[0-9]+"),
        (r"\P{L}", r"[^a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]"),
        # POSIX-like escapes
        (r"\p{Alpha}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\p{Digit}+", r"[0-9]+"),
        (r"\p{Alnum}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9]+"),
        (r"\p{Space}", r"[ \t\n\r\f\v]"),
        (r"\p{Z}", r"[ \t\n\r\f\v]"),
        # Shorthand forms (without braces)
        (r"\pL+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\pN+", r"[0-9]+"),
        (r"\PL", r"[^a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]"),
        (r"\PN", r"[^0-9]"),
        (r"^[\w\s\-\/\pL,.#;:()']+$", r"^[\w\s\-\/a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F,.#;:()']+$"),
        # `\p{X}` inside a character class with sibling chars: inline raw contents, never nest brackets.
        (r"[\p{Alnum}_]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9_]+"),
        (r"[\p{Alpha}_]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F_]+"),
        (
            r"^urn:tdm:[\p{Alnum}_]+:[\p{Alpha}]*:[\p{Alnum}_]+$",
            r"^urn:tdm:[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9_]+:[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]*:[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9_]+$",
        ),
        (r"[\p{Alpha}\p{Digit}_]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9_]+"),
        (r"[\p{Alpha}abc]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024Fabc]+"),
        (r"[abc\p{Alpha}]+", r"[abca-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"[^\p{Alpha}_]+", r"[^a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F_]+"),
        (r"[\pL_]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F_]+"),
        (r"[\p{Alpha}]+:[\p{Digit}]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+:[0-9]+"),
        (r"[\p{Alpha}_]+\p{Digit}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F_]+[0-9]+"),
        (r"\[\p{Alpha}\]", r"\[[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]\]"),
        # POSIX character classes nested inside `[...]` inline as raw class contents.
        (r"[[:alnum:]]", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9]"),
        (r"[[:alnum:]\/\_]", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9\/\_]"),
        (r"[[:digit:]]+", r"[0-9]+"),
        (r"[[:alpha:]_]+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F_]+"),
        (
            r"^([01]\d|2[0-3])(\[[[:alnum:]\/\_]+\])?$",
            r"^([01]\d|2[0-3])(\[[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9\/\_]+\])?$",
        ),
        # Only the braced form of a known name carries contents to complement.
        (r"[\PL_]", None),
        (r"[\P{Tibetan}_]", None),
        # A script class is a subset of the script, so its complement would admit characters the
        # validator still counts as part of it.
        (r"[\P{Greek}_]", None),
        (r"[\P{Cyrillic}_]", None),
        (r"[\P{Katakana}_]", None),
        (r"[\P{Hangul}_]", None),
        (r"[\P{Han}_]", None),
        (r"[\P{Hiragana}_]", None),
        (r"[\p{Tibetan}_]+", None),
        # Negated POSIX class `[:^X:]` and unknown POSIX names \u2014 bail out.
        (r"[[:^alnum:]_]", None),
        (r"[[:greek:]_]", None),
        # A nested class stands for one side of an operator.
        (r"[\p{N}&&[5]]", r"[5]"),
        # A side spelled with a shorthand class carries no codepoints to work out.
        (r"[\p{L}&&\w]", None),
        (r"[\p{L}&&[\w]]", None),
        # A nested class opening on `]` names it as a member, leaving the operator's side unreadable.
        (r"[\p{L}&&[]]", None),
        # A negated POSIX class cannot compose with the members beside it, on either side.
        (r"[\p{L}&&[:^alnum:]]", None),
        # Nothing is left for the class to admit.
        (r"[\p{L}&&\p{N}]", None),
        # Brace hex naming no digits, or a codepoint past the last one.
        (r"[a\x{}b]", None),
        (r"[a\x{110000}]", None),
        # A class that never closes, with and without an operator inside it.
        (r"[\p{L}&&[a]", None),
        (r"[\p{L}&&[:alpha]", None),
        (r"[\p{L}[a]", None),
        # No translation needed (already valid Python regex)
        (r"[a-z]+", None),
        (r"^\d+$", None),
        # Unsupported escapes (no translation available)
        (r"\p{Tibetan}", None),
        (r"\p{Script=Latin}", None),
    ],
)
def test_normalize_regex(pattern, expected):
    assert normalize_regex(pattern) == expected
    if expected:
        # FutureWarning "Possible nested set" means residual bracket nesting — translation didn't fully flatten.
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            re.compile(expected)


@pytest.mark.parametrize(
    ("pattern", "matching", "rejected"),
    [
        (r"[\P{L}_]", "_1 ", "aZ"),
        (r"[\P{Alnum}_]+", "_-!", "a1"),
        (r"[\P{M}\p{M}]", "a1́", ""),
    ],
)
def test_negated_property_inside_class(pattern, matching, rejected):
    compiled = re.compile(normalize_regex(pattern))
    for char in matching:
        assert compiled.fullmatch(char), char
    for char in rejected:
        assert not compiled.fullmatch(char), char


_ENGINE_AGREEMENT = [
    # `||` is not an operator in the engine behind validation - it names a literal `|`.
    (r"^[\p{L}||\p{N}]$", "a1|", "!"),
    (r"^[\p{L}||\p{M}||\p{Z}||\p{S}||\p{N}||\p{P}]$", "a1 .$|", "\x01"),
    # `&&` keeps what both sides admit, and a class nested inside another adds to it.
    (r"^[\p{Print}&&[^|:/]]$", "a& ", "|:/"),
    (r"^[[\p{L}]\p{N}]$", "a1", "[]!"),
    # `~~` keeps what only one side admits.
    (r"^[\p{L}~~\p{N}]$", "a1", "~!"),
    # A class reaching the last codepoint leaves no gap above it.
    (r"^[\p{L}\x{10FFFF}~~[a]]$", "b\U0010ffff", "a"),
    # PCRE brace hex escapes.
    (r"^[a\x{60}]$", "a`", "b"),
    (r"^([$\-._+!*\x{60}(),;/?:@=&\w]|%([0-9a-fA-F?]{2}|[0-9a-fA-F?]?[*]))+$", "a`$_", " \t"),
    # Unicode script names.
    (r"^[A-Za-z \p{Han}\p{Katakana}\p{Hiragana}\p{Hangul}-]$", "aZ -中アあ가", "1!"),
]


@pytest.mark.parametrize(
    ("pattern", "matching", "rejected"), _ENGINE_AGREEMENT, ids=[item[0] for item in _ENGINE_AGREEMENT]
)
def test_translation_agrees_with_the_validator(pattern, matching, rejected):
    translated = normalize_regex(pattern)
    assert translated is not None
    compiled = re.compile(translated)
    validator = jsonschema_rs.validator_for({"type": "string", "pattern": pattern}, pattern_options=FANCY_REGEX_OPTIONS)
    for char in matching:
        assert compiled.fullmatch(char), char
        assert validator.is_valid(char), char
    for char in rejected:
        assert not compiled.fullmatch(char), char
        assert not validator.is_valid(char), char


_COMPLEMENT_PROBE = "aZ0_ -!.\t\néÀ́ €中\U0001f600"


@pytest.mark.parametrize("name", sorted(set(_UNICODE_PROPERTY_RAW_MAP) - _PARTIAL_SCRIPT_CLASSES))
def test_negated_property_is_the_exact_complement(name):
    positive = re.compile(f"[{_UNICODE_PROPERTY_RAW_MAP[name]}]")
    negated = re.compile(normalize_regex(rf"[\P{{{name}}}]"))
    for char in _COMPLEMENT_PROBE:
        assert bool(negated.fullmatch(char)) is not bool(positive.fullmatch(char)), char


_PROPERTY_NAMES = ("L", "Lu", "Ll", "N", "Nd", "Alpha", "Digit", "XDigit", "Alnum", "Space", "Punct", "Upper", "ASCII")
_PROPERTY_FRAGMENTS = st.sampled_from([f"\\p{{{name}}}" for name in _PROPERTY_NAMES] + ["\\pL", "\\pN", "\\pP", "\\pZ"])
_INSIDE_CLASS_EXTRAS = st.text(alphabet="abcXYZ_-0", max_size=4)


@st.composite
def _patterns_with_properties(draw: st.DrawFn) -> str:
    parts: list[str] = []
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        choice = draw(st.sampled_from(["literal", "in_class", "out_class"]))
        if choice == "literal":
            parts.append(draw(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=3)))
        elif choice == "in_class":
            extras = draw(_INSIDE_CLASS_EXTRAS)
            prop = draw(_PROPERTY_FRAGMENTS)
            negate = "^" if draw(st.booleans()) else ""
            parts.append(f"[{negate}{extras}{prop}]")
        else:
            parts.append(draw(_PROPERTY_FRAGMENTS))
        parts.append(draw(st.sampled_from(["", "+", "*", "?"])))
    return "".join(parts)


@given(_patterns_with_properties())
@settings(suppress_health_check=list(HealthCheck), max_examples=200)
def test_normalize_regex_never_produces_nested_classes(pattern: str) -> None:
    result = normalize_regex(pattern)
    if result is None:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        re.compile(result)


@given(st.data())
@settings(suppress_health_check=list(HealthCheck))
def test_update_quantifier_random(data):
    # Generate a regex pattern
    pattern = data.draw(st.text(min_size=1).filter(is_valid_regex))

    # Generate optional length constraints
    min_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())
    max_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())

    # Ensure min_length <= max_length if both are present
    assume(
        max_length is None
        or min_length is None
        or (min_length <= max_length and not (min_length is None and max_length is None))
    )

    # Apply length constraints
    modified_pattern = update_quantifier(pattern, min_length, max_length)

    assume(pattern != modified_pattern)

    # Ensure the modified pattern is a valid regex
    assert is_valid_regex(modified_pattern)

    # Generate a string matching the modified pattern
    generated = data.draw(st.from_regex(modified_pattern, fullmatch=True, alphabet=st.characters(codec=None)))

    # A bound the rewrite cannot fold into the quantifiers stays with the schema, so only what the
    # pattern claims on its own has to hold here - that claim is what decides whether a bound is dropped.
    claimed_min, claimed_max = pattern_length_bounds(modified_pattern)
    assert len(generated) >= claimed_min, (
        f"Generated string '{generated}' is shorter than the claimed {claimed_min}\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
    )
    if claimed_max is not None:
        assert len(generated) <= claimed_max, (
            f"Generated string '{generated}' is longer than the claimed {claimed_max}.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
        )
    assert re.search(pattern, generated), (
        f"Generated string '{generated}' does not match the pattern.\nOriginal pattern: {pattern}\nModified pattern: {modified_pattern}"
    )


def is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except (re.error, RecursionError):
        return False


@pytest.mark.parametrize(
    "pattern",
    [
        # LITERAL
        "a",
        "0",
        r"\.",
        r"\*",
        r"\\",
        r"\^",
        r"\$",
        r"\|",
        r"\(",
        r"\)",
        r"\[",
        r"\{",
        r"\+",
        r"\?",
        # NOT_LITERAL
        r"[^a]",
        # ANY
        ".",
        # AT (anchors)
        "^a",
        "a$",
        r"\ba\b",
        r"\Ba\B",
        r"\Aa",
        r"a\Z",
        # IN (character classes)
        "[abc]",
        "[a-z]",
        "[^abc]",
        r"[\d]",
        r"[a-z\d_]",
        "[a-zA-Z0-9]",
        # Non-printable chars in character classes
        f"[{chr(0x00)}-{chr(0x08)}]+",
        f"[{chr(0x200B)}]+",
        # CATEGORY
        r"\d",
        r"\D",
        r"\w",
        r"\W",
        r"\s",
        r"\S",
        # BRANCH
        "a|b",
        "a|b|c",
        "(a)|(b)",
        # SUBPATTERN
        "(abc)",
        "(?:abc)",
        "((a)(b))",
        # Subpatterns with inline flags
        "(?i:abc)",
        "(?-i:abc)",
        "(?im:abc)",
        "(?i-m:abc)",
        # MAX_REPEAT
        "a*",
        "a+",
        "a?",
        "a{3}",
        "a{2,5}",
        "a{2,}",
        "(ab)*",
        "(ab)+",
        "[a-z]*",
        # MIN_REPEAT
        "a*?",
        "a+?",
        "a??",
        "a{2,5}?",
        # Combinations
        "^[a-z]+$",
        "abc",
        r"^[A-Z]{2}\d{3}-[a-z]+$",
        "(a|b)+",
        r"((\d{2})+)",
        # ASSERT / ASSERT_NOT (lookahead and lookbehind)
        r"(?=abc)",
        r"(?!abc)",
        r"(?<=abc)",
        r"(?<!abc)",
        r"\w+(?<!\s)",
        r"(?=\d)\w+",
        # GROUPREF (backreference)
        r"(foo)\1",
        r"([a-z])\1+",
        # GROUPREF_EXISTS (conditional group)
        r"(x)(?(1)a|b)",
        # ATOMIC_GROUP
        pytest.param("(?>abc)", marks=SKIP_BEFORE_PY11),
        pytest.param("(?>\\d+)", marks=SKIP_BEFORE_PY11),
    ],
)
def test_serialize_roundtrip(pattern):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    re.compile(serialized)

    # Idempotent
    assert _serialize(list(sre_parse.parse(serialized))) == serialized

    # Semantically equivalent
    for s in ["", "a", "abc", "ABC", "123", "a-b", "\t\n", "\x00"]:
        assert bool(re.search(pattern, s)) == bool(re.search(serialized, s))


@pytest.mark.parametrize(
    "pattern",
    [
        pytest.param("a*+", marks=SKIP_BEFORE_PY11),
        pytest.param("a++", marks=SKIP_BEFORE_PY11),
        pytest.param("a{2,5}+", marks=SKIP_BEFORE_PY11),
    ],
)
def test_serialize_possessive(pattern):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    re.compile(serialized)
    assert _serialize(list(sre_parse.parse(serialized))) == serialized


@pytest.mark.parametrize("char", list(string.printable))
def test_serialize_printable_char(char):
    parsed = sre_parse.parse(re.escape(char))
    serialized = _serialize(list(parsed))
    assert re.compile(serialized).fullmatch(char)


@pytest.mark.parametrize(
    ("char", "name"),
    [
        ("\x00", "null"),
        ("\x01", "SOH"),
        ("\x07", "BEL"),
        ("\x08", "BS"),
        ("\x1b", "ESC"),
        ("\x7f", "DEL"),
        ("\x80", "0x80"),
        ("\xff", "0xff"),
    ],
)
def test_serialize_control_char(char, name):
    parsed = sre_parse.parse(re.escape(char))
    serialized = _serialize(list(parsed))
    assert re.compile(serialized).fullmatch(char)


@pytest.mark.parametrize(
    ("char", "name"),
    [
        ("\u00e9", "e-acute"),
        ("\u00f1", "n-tilde"),
        ("\u0100", "A-macron"),
        ("\u4e2d", "CJK"),
        ("\U0001f600", "emoji"),
    ],
)
def test_serialize_unicode_char(char, name):
    parsed = sre_parse.parse(re.escape(char))
    serialized = _serialize(list(parsed))
    assert re.compile(serialized).fullmatch(char)


@pytest.mark.parametrize(
    ("pattern", "should_match", "should_not_match"),
    [
        ("[^a]", "b", "a"),
        ("[^0-9]", "a", "5"),
        (r"[^\d]", "a", "5"),
        ("[^a-z]", "A", "a"),
    ],
)
def test_serialize_negated_class(pattern, should_match, should_not_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(should_match)
    assert not compiled.fullmatch(should_not_match)


@pytest.mark.parametrize(
    ("pattern", "samples_in", "samples_out"),
    [
        ("[a-z]", list("abcxyz"), list("ABC019")),
        ("[A-Z]", list("ABCXYZ"), list("abc019")),
        ("[0-9]", list("0159"), list("abcABC")),
        ("[a-zA-Z0-9]", list("aZ0"), list("!@# ")),
        ("[a-z0-9_-]", list("a0_-"), list("!@A")),
    ],
)
def test_serialize_char_class_range(pattern, samples_in, samples_out):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    for ch in samples_in:
        assert compiled.fullmatch(ch)
    for ch in samples_out:
        assert not compiled.fullmatch(ch)


@pytest.mark.parametrize(
    ("pattern", "should_match"),
    [
        (r"a\.b", "a.b"),
        (r"a\*b", "a*b"),
        (r"a\+b", "a+b"),
        (r"a\?b", "a?b"),
        (r"\(x\)", "(x)"),
        (r"\[x\]", "[x]"),
        (r"a\\b", "a\\b"),
        (r"\^\$", "^$"),
    ],
)
def test_serialize_escaped_literal(pattern, should_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    assert re.compile(serialized).fullmatch(should_match)


@given(pattern=st.text(min_size=1, max_size=80).filter(is_valid_regex))
@settings(max_examples=500, suppress_health_check=list(HealthCheck), deadline=None)
def test_serialize_random_pattern(pattern):
    parsed = sre_parse.parse(pattern)
    try:
        serialized = _serialize(list(parsed))
    except InternalError:
        return

    re.compile(serialized)
    assert _serialize(list(sre_parse.parse(serialized))) == serialized


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_pcre_pattern_in_response_schema_during_dependency_analysis(cli, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/owners": {
                "get": {
                    "operationId": "listOwners",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Owner"},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/owners/{ownerId}": {
                "get": {
                    "operationId": "getOwner",
                    "parameters": [{"name": "ownerId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        # allOf triggers canonicalize() call in dependency analysis
                                        "allOf": [
                                            {"$ref": "#/components/schemas/Owner"},
                                            {"description": "An owner"},
                                        ]
                                    }
                                }
                            },
                        }
                    },
                }
            },
        },
        components={
            "schemas": {
                "Owner": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        # PCRE Unicode property escape - this caused the crash
                        "firstName": {"type": "string", "pattern": r"^[\p{L}]+([ '-][\p{L}]+){0,2}\.?$"},
                        "lastName": {"type": "string", "pattern": r"^[\p{L}]+([ '-][\p{L}]+){0,2}\.?$"},
                    },
                }
            }
        },
    )

    @app.route("/owners")
    def list_owners():
        return jsonify([{"id": 1, "firstName": "John", "lastName": "Doe"}])

    @app.route("/owners/<int:owner_id>")
    def get_owner(owner_id):
        return jsonify({"id": owner_id, "firstName": "John", "lastName": "Doe"})

    # This should not crash with SchemaError about invalid regex
    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=1",
            "--phases=examples",
        )
        == snapshot_cli
    )


def test_response_schema_is_not_mutated(cli, ctx, snapshot_cli):
    # See GH-2749
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "Container Image API", "version": "1.0.0"},
        "paths": {
            "/container": {
                "post": {
                    "summary": "Create a container image",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"container_image": {"$ref": "#/components/schemas/ContainerImage"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Successful response",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "container_image": {"$ref": "#/components/schemas/ContainerImage"}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "ContainerImage": {
                    "description": "A container image",
                    "type": "string",
                    "maxLength": 500,
                    "pattern": "^[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*(\\/[a-z0-9]+((\\.|_|__|-+)[a-z0-9]+)*)*(:[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}|@sha256:[a-fA-F0-9]{64}){0,1}$",
                    "example": "renku/renkulab-py:3.10-0.18.1",
                }
            }
        },
    }

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/container", methods=["POST"])
    def create_container():
        example_value = raw_schema["components"]["schemas"]["ContainerImage"]["example"]
        response_body = {"container_image": example_value}
        return jsonify(response_body), 200

    assert cli.run_openapi_app(app, "-call", "--phases=fuzzing", "-n 1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unicode_surrogate_pattern_in_query_parameter(cli, ctx, snapshot_cli):
    # Pattern from amazonaws.com/cleanrooms schema - surrogate code points are invalid in regex
    invalid_pattern = "([\\u0020-\\uD7FF\\uE000-\\uFFFD\\uD800\\uDBFF-\\uDC00\\uDFFF\\t\\r\\n]){0,255}"

    app, _ = ctx.openapi.make_flask_app(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "description",
                            "in": "query",
                            "schema": {"type": "string", "pattern": invalid_pattern},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/test")
    def test_endpoint():
        return jsonify({"status": "ok"})

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=10",
            "--phases=fuzzing",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True, replace_invalid_component=True)
def test_unicode_surrogate_pattern_in_request_body(cli, ctx, snapshot_cli):
    # Surrogate code point range - invalid in regex engine
    invalid_pattern = "[\\uD800-\\uDBFF]"

    app, _ = ctx.openapi.make_flask_app(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string", "pattern": invalid_pattern},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/test", methods=["POST"])
    def test_endpoint():
        return jsonify({"status": "ok"})

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=10",
            "--phases=fuzzing",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("pattern", "chars", "expected"),
    [
        # Literal / outside brackets — every match must contain /
        ("arn:aws:[a-z]+/[a-z]+", "/", True),
        # Multiple literal slashes
        ("arn:aws:[a-z]+/[a-z]+/[0-9]+", "/", True),
        # Slash inside character class — optional, not required
        ("[a-z/]+", "/", False),
        # No slash at all
        ("^[a-zA-Z0-9_-]+$", "/", False),
        # Slash in branch — only required if ALL branches contain it
        ("(foo/bar|baz/qux)", "/", True),
        # Slash in only one branch — not required (the other branch can match without it)
        ("(foo/bar|baz)", "/", False),
        # Slash after quantifier (required: the literal is not quantified)
        ("[a-z]+/[0-9]+", "/", True),
        # Pattern with anchors and literal slash
        ("^arn:aws:appsync:[A-Za-z0-9_/.-]+/apis/[0-9A-Za-z_-]+$", "/", True),
        # Slash in zero-or-more quantified group — not required (0 repetitions possible)
        ("(a/b)*", "/", False),
        # Slash in one-or-more quantified group — required (at least 1 repetition)
        ("(a/b)+", "/", True),
        # Slash in optional group — not required
        ("(a/b)?", "/", False),
        # Slash in fixed repetition — required
        ("(a/b){2}", "/", True),
        # Slash in {0,n} repetition — not required
        ("(a/b){0,3}", "/", False),
        # Deeply nested: branch inside group with slash
        ("((foo/bar|baz/qux))+", "/", True),
        # Only one nested branch has slash
        ("((foo/bar|baz))+", "/", False),
        # Detect literal { outside brackets
        ("prefix{suffix", "{", True),
        # Detect literal } outside brackets
        ("prefix}suffix", "}", True),
        # { inside character class
        ("[{abc}]+", "{", False),
        # Empty pattern
        ("", "/", False),
        # Slash is the entire pattern
        ("/", "/", True),
        # Invalid regex — return False (caller handles separately)
        (r"\P{C}*", "/", False),
        # Real AWS ARN pattern
        (
            "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+",
            "/",
            True,
        ),
        # Real AWS pattern where / only appears inside a character class — not required
        (
            "arn:(aws[a-zA-Z-]*)?:[a-z]+:([a-z]{2}((-gov)|(-iso(b?)))?-[a-z]+-\\d{1})?:(\\d{12})?:[a-zA-Z0-9-_/:.]+",
            "/",
            False,
        ),
        # Pattern where / is only inside [...] — NOT required
        ("[a-zA-Z0-9/._-]+", "/", False),
        # Multiple chars: pattern with / matches against "/{}
        ("arn:aws:[a-z]+/[a-z]+", "/{}", True),
        # Multiple chars: pattern with { matches against "/{}
        ("prefix{suffix", "/{}", True),
        # Multiple chars: none present
        ("^[a-zA-Z0-9_-]+$", "/{}", False),
    ],
)
def test_pattern_requires_literal(pattern, chars, expected):
    assert pattern_requires_literal(pattern, chars) == expected


ALNUM = string.ascii_letters + string.digits


@pytest.mark.parametrize(
    ("pattern", "allowed", "expected"),
    [
        ("abc123", ALNUM, False),
        ("abc-123", ALNUM, True),
        ("arn:aws:s3:::bucket", ALNUM, True),
        # Real corpus case: ARN in a header parameter
        ("arn:[a-z0-9-\\.]{1,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}:[a-z0-9-\\.]{0,63}", ALNUM, True),
        ("[a-z0-9]+", ALNUM, False),
        ("[._-]+", ALNUM, True),
        ("[a._-]+", ALNUM, False),
        ("[!-/]", ALNUM, True),
        ("[0-9]", ALNUM, False),
        ("[^:]", ALNUM, False),
        ("[^a]", ALNUM, False),
        ("[-]*", ALNUM, False),
        ("[-]+", ALNUM, True),
        ("[-]?", ALNUM, False),
        (":{2}", ALNUM, True),
        ("[-]{0,3}", ALNUM, False),
        ("(foo-bar|baz-qux)", ALNUM, True),
        ("(foo-bar|baz)", ALNUM, False),
        ("((foo:bar|baz:qux))+", ALNUM, True),
        ("((foo:bar|baz))+", ALNUM, False),
        (".", ALNUM, False),
        ("\\d+", ALNUM, False),
        ("\\w+", ALNUM, False),
        (":", ALNUM, True),
        ("", ALNUM, False),
        ("[", ALNUM, False),
        ("abc", "abc", False),
        ("abcd", "abc", True),
    ],
)
def test_pattern_requires_char_outside(pattern, allowed, expected):
    assert pattern_requires_char_outside(pattern, allowed) == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("^[a-z]+$", True),
        # Surrogate code points, which the validator's regex engine refuses.
        ("[\\uD800-\\uDBFF]", False),
        # An incomplete quantifier, valid in Python and not in ECMA 262.
        ("[A-Z]{,3}", False),
    ],
)
def test_is_valid_jsonschema_rs_regex(pattern, expected):
    assert is_valid_jsonschema_rs_regex(pattern) is expected


def test_pattern_the_validator_cannot_compile_is_dropped(ctx):
    # Such a pattern constrains nothing during validation, and keeping it would only stop the rest
    # of the schema from being modeled.
    schema = ctx.openapi.build_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string", "pattern": "[A-Z]{,3}", "maxLength": 5},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    operation = schemathesis.openapi.from_dict(schema)["/items"]["GET"]
    parameter = next(iter(operation.query))

    assert "pattern" not in parameter.optimized_schema
    assert parameter.optimized_schema["maxLength"] == 5


def test_quantifier_rewrite_the_validator_cannot_compile_is_not_taken():
    # Folding a bound this large into the pattern makes a quantifier the regex engine refuses; the
    # pattern and the bound both stay as they were.
    schema = {"type": "string", "pattern": "^.{1,}$", "maxLength": 2147483647}

    update_pattern_in_schema(schema)

    assert schema == {"type": "string", "pattern": "^.{1,}$", "maxLength": 2147483647}


def test_quantifier_rewrite_within_reach_is_taken():
    schema = {"type": "string", "pattern": "^.{1,}$", "maxLength": 10}

    update_pattern_in_schema(schema)

    assert schema == {"type": "string", "pattern": "^.{1,10}$"}


def test_rewrite_keeps_the_upper_bound_finite_when_parts_cannot_be_pinned():
    # Optional alternations block exact length, and an open-ended rewrite then aims generation at
    # strings orders of magnitude past the bound, which the length check throws away.
    pattern = r"^arn:aws(-cn|-us-gov)?:[a-z0-9-]*:[a-z0-9-]*:([0-9]{12})?:.+$"

    assert pattern_length_bounds(update_quantifier(pattern, 1000, 1000)) == (1000, 1000)
