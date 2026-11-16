import re
import string
import warnings
from itertools import product

import jsonschema_rs
import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse  # type: ignore[no-redef]

from schemathesis.core.errors import InternalError
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS
from schemathesis.core.jsonschema.patterns import (
    _PARTIAL_SCRIPT_CLASSES,
    _UNICODE_PROPERTY_RAW_MAP,
    CharacterRules,
    _serialize,
    character_rules,
    compile_ecma_pattern,
    is_valid_jsonschema_rs_regex,
    is_valid_python_regex,
    matches_every_string,
    normalize_regex,
    pattern_length_bounds,
    pattern_length_is_unreachable,
    pattern_requires_char_outside,
    pattern_requires_literal,
    pin_pattern_length,
    update_quantifier,
)


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        ("a", None, 3, "^a{1,3}$"),
        ("a", 3, 3, "^a{3}$"),
        ("a", 0, 3, "^a{1,3}$"),
        ("}?", 1, None, r"\}{1}"),
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
        (".{1,5}", None, 3, "^.{1,3}$"),
        (".{0,3}", 1, None, ".{1,3}"),
        (".{2,}", 1, 3, "^.{2,3}$"),
        (".{1,5}?", None, 3, "^.{1,3}$"),
        (".{0,3}?", 1, None, ".{1,3}"),
        (".{2,}?", 1, 3, "^.{2,3}$"),
        (".{1,5}+", None, 3, "^.{1,3}$"),
        (".{0,3}+", 1, None, ".{1,3}"),
        (".{2,}+", 1, 3, "^.{2,3}$"),
        ("[a-z]", None, 5, "^[a-z]{1,5}$"),
        ("[a-z]", 3, None, "[a-z]{3,}"),
        ("[a-z]", 3, 5, "^[a-z]{3,5}$"),
        ("[a-z]", 1, 5, "^[a-z]{1,5}$"),
        ("a|b", 1, 5, "^[ab]{1,5}$"),
        ("[a-z]*", None, 5, "^[a-z]{0,5}$"),
        ("[a-z]*", 3, None, "[a-z]{3,}"),
        ("[a-z]*", 3, 5, "^[a-z]{3,5}$"),
        ("[a-z]*", 1, 5, "^[a-z]{1,5}$"),
        ("^[a-z]*", None, 5, "^[a-z]{0,5}$"),
        ("^[a-z]*", 3, 5, "^[a-z]{3,5}$"),
        ("^[a-z]+", 0, 5, "^[a-z]{1,5}$"),
        ("^[a-z]*$", None, 5, "^[a-z]{0,5}$"),
        ("^[a-z]*$", 3, 5, "^[a-z]{3,5}$"),
        ("^[a-z]+$", 0, 5, "^[a-z]{1,5}$"),
        ("^.+$", 0, 5, "^.{1,5}$"),
        ("^.{0,1}$", 0, 5, "^.{0,1}$"),
        ("^.$", 0, 5, "^.{1}$"),
        # Fully anchored single-char content matches one character, so the budget may only narrow it.
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
        ("abc*def*", 1, 3, "abc*def*"),
        ("[bc]*[de]*", 1, 3, "[bc]*[de]*"),
        ("[bc]3", 1, 3, "[bc]3"),
        ("b{30,35}", 1, 3, "b{30,35}"),
        ("b{1,3}", 10, None, "b{1,3}"),
        ("b", 0, 0, "b"),
        ("b$", None, None, "b$"),
        ("b$", 0, None, "b$"),
        ("}?", 0, None, "}?"),
        # The literal's length falls outside the bounds.
        ("^0$", 2, 2, "^0$"),
        ("^0$", 2, None, "^0$"),
        ("^0$", 0, 0, "^0$"),
        ("^abc[0-9]*$", None, 5, "^abc[0-9]{0,2}$"),
        ("^-[a-z]{1,10}-$", None, 4, "^-[a-z]{1,2}-$"),
        (r"^[a-z]{2,4}-\d{4,15}$", 7, 7, r"^[a-z]{2}-\d{4}$"),
        (r"^[a-z]{2,4}-\d{4,15}$", 20, 20, r"^[a-z]{4}-\d{15}$"),
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
        # Greedy would collapse the suffix to `{0}`; each slot instead gets the budget minus the others' minimums.
        (
            r"^[a-zA-Z0-9]+([-a-zA-Z0-9]?[a-zA-Z0-9])*$",
            5,
            64,
            r"^[a-zA-Z0-9]{1,64}([\-a-zA-Z0-9]{0,1}[a-zA-Z0-9]){0,63}$",
        ),
        (r"^\+[0-9]{5,}$", 6, 6, r"^\+[0-9]{5}$"),
        (r"^abcd$", 50, 50, r"^abcd$"),
        # An exact length picks a shape with no `{0}` collapse on either optional slot when one fits.
        ("^[a-z]*-[0-9]*$", 3, 3, "^[a-z]{1}-[0-9]{1}$"),
        (r"^[+][\s0-9()-]+$", 1, 20, r"^\+[\s0-9()\-]{1,19}$"),
        (r"^[\+][\s0-9()-]+$", 1, 20, r"^\+[\s0-9()\-]{1,19}$"),
        ("^abc[0-9]{1,3}def[a-z]{2,5}ghi$", 12, 12, "^abc[0-9]{1}def[a-z]{2}ghi$"),
        ("^(((?:DB|BR)[-a-zA-Z0-9_]+),?){1,}$", None, 6000, r"^(((?:DB|BR)[\-a-zA-Z0-9_]{1,}),{0,1}){1,2000}$"),
        # `\*?` stays optional (`{0}` would reject "geo:abc*") while `\w*` tightens to the remaining budget.
        (r"^geo:\w*\*?$", 5, 200, r"^geo:\w{0,196}\*{0,1}$"),
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
        # A variable repeat cannot carry `maxLength`; an exact length goes to the leading slot and the repeat drops out.
        (r"^prefix[|]+(?:,prefix[|]+)*$", 4000, 4000, r"^prefix\|{3994}(?:,prefix\|{1,}){0}$"),
        (r"^bar\.spam\.[^,]+(?:,bar\.spam\.[^,]+)*$", 10, 10, r"^bar\.spam\.[^,]{1}(?:,bar\.spam\.[^,]{1,}){0}$"),
        # `()?` stays optional while `8+` tightens to the budget.
        (r"^\008+()?$", None, 2, r"^\x008{1}(){0,1}$"),
        (r"^\008+()?$", 2, None, r"^\x008{1,}(){0,1}$"),
        (r"^000(000)?$", 4, 5, r"^000(000)?$"),
        ("(abc)+", 1, 10, "^(abc){1,3}$"),
        ("(hello){2,5}", None, 12, "^(hello){2}$"),
        ("(abcd)*", 3, 7, "^(abcd){1}$"),
        ("^()?$", 4, 5, "^()?$"),
        (r"(?i).*", 1, 5, r"(?i)^.{1,5}$"),
        (r"(?i)[a-z]+", 2, 8, r"(?i)^[a-z]{2,8}$"),
        (r"(?im)[a-z]+", 1, 5, r"(?im)^[a-z]{1,5}$"),
        (r"(?s).+", 1, 3, r"(?s)^.{1,3}$"),
        (r"\b[a-z]+\b", 2, 5, r"\b[a-z]{2,5}\b"),
        (r"\B\d+\B", 1, 3, r"\B\d{1,3}\B"),
        # A one-character class of a non-printable character collapses to a `\uXXXX` literal.
        (f"[{chr(0x200B)}]+", 2, 5, "^\\u200b{2,5}$"),
        # A non-printable range stays a class.
        (f"[{chr(0x200B)}-{chr(0x200D)}]+", 2, 5, "^[\\u200b-\\u200d]{2,5}$"),
        ("(?i:[a-z])+", 2, 5, "^(?i:[a-z]){2,5}$"),
        ("(?im:[a-z])+", 1, 4, "^(?im:[a-z]){1,4}$"),
        ("(?-i:[A-Z])+", 2, 4, "^(?-i:[A-Z]){2,4}$"),
        ("(?i-m:[a-z])+", 1, 3, "^(?i-m:[a-z]){1,3}$"),
        (r"^()+$", 0, 5, "^(){0}$"),
        (r"^()+$", 1, 5, r"^()+$"),
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
        (r"(a|bb)+", 4, 8, "^(a|bb){2,8}$"),
        (r"^(abc)+(def)+$", 6, 6, r"^(abc){1}(def){1}$"),
        (r"^(abc)+(def)+$", 9, 9, r"^(abc){1}(def){2}$"),
        (r"^(ab)+(cd)+(ef)+$", 6, 6, r"^(ab){1}(cd){1}(ef){1}$"),
        (r"^(ab)+(cd)+(ef)+$", 10, 10, r"^(ab){1}(cd){1}(ef){3}$"),
        (r"^(\d{3})+(\w{2})+$", 10, 10, r"^(\d{3}){2}(\w{2}){2}$"),
        (r"^(abc)+\d+$", 4, 10, r"^(abc){1,3}\d{1,7}$"),
        (r"^(abc)+(\d)+$", 7, 7, r"^(abc){1}(\d){4}$"),
        (r"^abc(\d{3})+$", 6, 12, r"^abc(\d{3}){1,3}$"),
        (r"^([a-z]+(?<!\s))+$", 1, 5, r"^([a-z]{1,}(?<!\s)){1,5}$"),
        (r"^([a-z]+(?!\s))+$", 1, 5, r"^([a-z]{1,}(?!\s)){1,5}$"),
        (r"^([a-z]+(?<=\s))+$", 1, 5, r"^([a-z]{1,}(?<=\s)){1,5}$"),
        (r"^([a-z]+(?=\s))+$", 1, 5, r"^([a-z]{1,}(?=\s)){1,5}$"),
        # Branch alternation with 1 or 2 chars per repetition: the count leaves room for `"a" * 100`.
        (r"^[a-z0-9]([a-z0-9]|-[a-z0-9])*$", 1, 100, r"^[a-z0-9]([a-z0-9]|-[a-z0-9]){0,99}$"),
        (r"^(foo|bar)+$", 3, 12, r"^(foo|bar){1,4}$"),
        # The outer bound is finite and the inner varies, so the outer count cannot encode `maxLength`.
        (r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", 1, 63, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"),
        # A variable optional group absorbs `minLength` but cannot encode `maxLength`.
        (r"^([a-z][a-z]*)?$", 1, 5, r"^([a-z][a-z]*)?$"),
        # The middle keeps `{0,2}` (`{0}` rejects "Doe-Smith") and leaves `maxLength` open; the first slot tightens.
        (
            r"^[a-zA-Z]+([ '-][a-zA-Z]+){0,2}\.?$",
            1,
            30,
            r"^[a-zA-Z]{1,30}([ '\-][a-zA-Z]{1,}){0,2}\.{0,1}$",
        ),
        # Required siblings cannot reach `minLength` together, so the pattern stays as is.
        (r"^[a-z]{1,3}_[a-z]{1,3}$", 10, 12, r"^[a-z]{1,3}_[a-z]{1,3}$"),
        # A fixed count never shrinks to fit the budget; shorter strings would miss the original pattern.
        (r"^a?b{2}c{2}d?$", 2, 5, r"^a?b{2}c{2}d?$"),
        (r"^\+[0-9]{5}$", 2, 5, r"^\+[0-9]{5}$"),
        # An unbounded nested inner stays open; only the outer count carries the budget.
        (r"^([a-z]+)+$", 1, 100, r"^([a-z]{1,}){1,100}$"),
        # A bounded outer over an unbounded inner cannot bound the slot, so the pattern stays as is.
        (r"^([a-z]+){2,5}$", 1, 50, r"^([a-z]+){2,5}$"),
        # The empty group adds no length, so the budget never lands on it and the pattern stays as is.
        (r"^a+()+$", 2, 5, r"^a+()+$"),
        # A finite sibling cannot absorb the `minLength` a pinned slot leaves; the rewrite bails without crashing.
        (r"^(.|a+){1,3}\d{1,3}$", 100, 100, r"^(.|a+){1,3}\d{1,3}$"),
        # A possessive part keeps what it took, so rewrites admit more: `0++0` matches nothing, `0{1,3}0` matches "00".
        ("0++0", None, 4, "0++0"),
        ("a{1,3}+a", None, 4, "a{1,3}+a"),
        ("(?:ab)++ab", None, 4, "(?:ab)++ab"),
        ("a+b++c", 1, 4, "a+b++c"),
        (r"^(a*+)[ab]$", 2, 3, r"^(a*+)[ab]$"),
        # Anchored only at the start: the budget still lands, and a maximum closes the pattern with `$`.
        ("^[a-zA-Z][a-zA-Z0-9_]*", 63, 63, "^[a-zA-Z][a-zA-Z0-9_]{62}$"),
        ("^[a-zA-Z][a-zA-Z0-9_]*", 1, 63, "^[a-zA-Z][a-zA-Z0-9_]{0,62}$"),
        ("^[a-z][0-9]+", 3, 6, "^[a-z][0-9]{2,5}$"),
        ("^foo[a-z]+", 4, 8, "^foo[a-z]{1,5}$"),
        ("^[a-z][0-9]+", 5, None, "^[a-z][0-9]{4,}"),
        ("^[A-Z]{2}[0-9]*$", 20, 30, "^[A-Z]{2}[0-9]{18,28}$"),
        # A slot shorter than its maximum leaves the rest of the budget to its siblings.
        (r"^[a-z]{1,3}[0-9]+$", None, 10, r"^[a-z]{1,3}[0-9]{1,9}$"),
        # A group is a part like any other, and its own repeat is where its share of the budget goes.
        ("arn:([a-z0-9-]+):forecast:.*:.*:.+", 20, 40, r"^arn:([a-z0-9\-]{1,23}):forecast:.{0,22}:.{0,22}:.{1,23}$"),
        ("^prefix-([a-z]+)-[0-9]+$", 12, 24, "^prefix-([a-z]{1,15})-[0-9]{1,15}$"),
        ("^([a-z]+)-([0-9]+)$", 6, 12, "^([a-z]{1,10})-([0-9]{1,10})$"),
        # A group wrapping the whole pattern is transparent to the budget; the quantifier inside still tightens.
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
        # Lookarounds right after `^` take no characters, so the budget lands on what follows them.
        ("^(?!aws:)[a-z]*$", 1, 128, "^(?!aws:)[a-z]{1,128}$"),
        ("^(?!aws:)(?=x)[a-z]*$", 1, None, "^(?!aws:)(?=x)[a-z]{1,}$"),
        ("^(?!a)[a-z]+-[0-9]*$", 4, 4, "^(?!a)[a-z]{1}-[0-9]{2}$"),
        ("^(?!aws:)$", 1, None, "^(?!aws:)$"),
        ("^a*(?:-+?)?", 0, 0, "^a{0}(?:-{1,}?){0}$"),
        ("^a*()*b*$", 0, 0, "^a{0}(){0}b{0}$"),
        ("^a(bc)x+$", None, 4096, "^a(bc)x{1,4093}$"),
        ("^[a-z](0{0,2})+", 1, None, "^[a-z](0{0,2}){1,}"),
        ("^.?-*", None, 1, "^.?-*"),
        ("^(a)$", None, 4096, "^(a{1})$"),
        ("^(a?)+b+$", 7, 12, "^(a?)+b+$"),
        ("^(?:(?:a|bb){2}){1,3}$", 4, None, "^(?:(?:a|bb){2}){1,3}$"),
        ("}", None, 1, r"^\}{1}$"),
        ("^", None, 4096, "^"),
        ("}?", 0, 0, r"^\}{0}$"),
        ("a+b", 0, 10, "a+b"),
        ("(ab)*", None, 1, "(ab)*"),
        ("^a+()+$", 1, None, "^a+()+$"),
        ("^a{1,3}.?", 4, None, "^a{1,3}.?"),
        ("^\\uffff+$", None, 4096, "^\\uffff{1,4096}$"),
        ("^(a?)+b+$", 0, 0, "^(a?)+b+$"),
        ("\\w+(?<!\\s)", None, 4096, "\\w+(?<!\\s)"),
        ("^(?-im:a)+$", None, 4096, "^(?-im:a){1,4096}$"),
        ("^(x{0,2})?x+.$", None, 3, "^(x{0,2}){0,1}x{1,2}.$"),
        ("^(?:ab|abcd)+$", None, 4096, "^(?:ab(?:|cd)){1,2048}$"),
        ("^(?:.+){2}0{2}(?:.+a{1,3})+?$", 2, 5, "^(?:.+){2}0{2}(?:.+a{1,3})+?$"),
        ("^(?=a)", 1, 3, "^(?=a)"),
        # Every slot keeps every count some string within the bounds needs, e.g. `geo:abcdef` and `a0000`.
        (r"^geo:\w*\*?$", None, 10, r"^geo:\w{0,6}\*{0,1}$"),
        (r"^[a-z]{1,3}[0-9]+$", 5, 10, r"^[a-z]{1,3}[0-9]{2,9}$"),
        (r"^(a?)+b+c+$", 7, 12, r"^(a{0,1}){1,}b{1,11}c{1,11}$"),
        # A `maxLength` at or above the regex repeat limit is no limit at all.
        ("^a{1,5}b{1,5}$", 11, 4294967295, "^a{1,5}b{1,5}$"),
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
        # Either slot may carry most of the length, so `minLength` stays with the schema.
        ("[a-z]+[0-9]+", 4, 8, "^[a-z]{1,7}[0-9]{1,7}$"),
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
    ("pattern", "min_length", "max_length", "value", "expected"),
    [
        # The rewrite must admit every length split the original allows; balanced per-slot caps would reject some.
        (r"^[a-zA-Z*]+[a-zA-Z0-9-]*$", 1, 128, "A" + "0" * 100, True),
        (r"^[a-zA-Z*]+[a-zA-Z0-9-]*$", 1, 128, "A" + "0" * 127, True),
        (r"^[a-zA-Z*]+[a-zA-Z0-9-]*$", 1, 128, "A" * 128, True),
        (r"^[a-z]{1,3}[0-9]+$", None, 10, "a" + "0" * 9, True),
        (r"^[a-z]{1,3}[0-9]+$", 1, 10, "a" + "0" * 9, True),
        # Matches every string, so it gets a narrower rewrite that must still let the digits fill the budget.
        (r"^(\+\d{1,3})?\d*", None, 10, "1234567890", True),
        (r"^(\+\d{1,3})?\d*", 1, 10, "7", True),
        (r"^(\+\d{1,3})?\d*", 1, 10, "1234567890", True),
        (r"^(\+\d{1,3})?\d*", 1, 10, "12345678901", False),
    ],
)
def test_update_quantifier_admits_uneven_slot_distributions(pattern, min_length, max_length, value, expected):
    rewritten = update_quantifier(pattern, min_length, max_length)
    assert bool(re.fullmatch(rewritten, value)) is expected, (rewritten, value)


# Ten optional characters plus a dash, then a fixed 36-character tail: 36 or 47 characters, never anything between.
SPLIT_UUID = r"^([0-9a-f]{10}-|)[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$"


# A long ARN, or a `$` followed by any number of two-character-or-longer segments: one character, or three and up.
SECRET_REFERENCE = r"(^arn:aws([a-z]|\-)*:secretsmanager:[a-z0-9-.]+:.*)|(\$(\.[\w_-]+(\[(\d+|\*)\])*)*)"


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        (SPLIT_UUID, 46, 46, True),
        (SPLIT_UUID, 36, 36, False),
        (SPLIT_UUID, 47, 47, False),
        (SPLIT_UUID, 46, 47, False),
        (SPLIT_UUID, 37, 45, True),
        (SECRET_REFERENCE, 2, 2, True),
        (SECRET_REFERENCE, 1, 1, False),
        (SECRET_REFERENCE, 3, 3, False),
        (r"^(ab)+$", 5, 5, True),
        (r"^(ab)+$", 4, 4, False),
        (r"^[a-z]{3}$", 1, 2, True),
        (r"^[a-z]+$", 5, 5, False),
        (r"^[a-z]+$", 0, 0, True),
        # Back-references carry a length the walk cannot follow, so nothing is ruled out.
        (r"^(a+)\1$", 3, 3, False),
        ("(?>abc)", None, 1, True),
        ("}", 4096, None, True),
        ("(foo)\\1", 2, 2, True),
        ("|", 3, 1, True),
        ("x{2}", None, 1, True),
        ("^(ab|c)", 0, 0, True),
        ("^(b*?)*?", None, 4096, False),
    ],
)
def test_pattern_length_is_unreachable(pattern, min_length, max_length, expected):
    assert pattern_length_is_unreachable(pattern, min_length, max_length) is expected


PIN_CASES = [
    (r"aws\.partner(/[\.\-_A-Za-z0-9]+){2,}", 256),
    (r"^[a-zA-Z0-9](-*[a-zA-Z0-9])*$", 256),
    (r"^(https?):\/\/([^\s]*)", 2048),
    (r"^(?!\s).+@([a-zA-Z0-9_\-\.]+)\.([a-zA-Z]{2,5})$", 256),
    (SECRET_REFERENCE, 1599),
    (r"^([A-Za-z](-|_|.)?)+$", 101),
    (r"^[a-z]+$", 40),
    (SPLIT_UUID, 47),
]


IOTTWINMAKER_ARN = r".*(^arn:((aws)|(aws-cn)|(aws-us-gov)):s3:::)([a-zA-Z0-9_-]+$).*"


FSX_ARN = (
    r"^arn:(?=[^:]+:fsx:[^:]+:\d{12}:)((|(?=[a-z0-9-.]{1,63})(?!\d{1,3}(\.\d{1,3}){3})"
    r"(?![^:]*-{2})(?![^:]*-\.)(?![^:]*\.-)[a-z0-9].*(?<!-)):){4}(?!/).{0,1024}$"
)


@pytest.mark.parametrize(("pattern", "length"), PIN_CASES)
def test_pin_pattern_length(pattern, length):
    # A quantifier left spanning a range multiplies into catastrophic backtracking, so pin one length.
    assert pattern_length_bounds(pin_pattern_length(pattern, length, length)) == (length, length)


@pytest.mark.parametrize(("pattern", "length"), PIN_CASES)
@settings(max_examples=5, suppress_health_check=list(HealthCheck), deadline=None)
@given(data=st.data())
def test_pinned_pattern_stays_within_the_original_language(pattern, length, data):
    pinned = pin_pattern_length(pattern, length, length)
    value = data.draw(st.from_regex(pinned, fullmatch=True, alphabet=st.characters(codec=None)))
    assert len(value) == length
    assert re.search(pattern, value), f"{value!r} does not match {pattern}"


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length"),
    [
        # Back-references are not analysable.
        (r"^(a+)\1$", 6, 6),
        # Nothing of that length exists.
        (SPLIT_UUID, 46, 46),
        # No bound to aim at.
        (r"^[a-z]+$", None, None),
        # Further than the walk goes.
        (r"^[a-z]+$", 100_000, 100_000),
    ],
)
def test_pin_pattern_length_leaves_unsupported_shapes_alone(pattern, min_length, max_length):
    assert pin_pattern_length(pattern, min_length, max_length) == pattern


@given(st.data())
# Most drawn patterns come back unpinned and get filtered out, so the count carries the few that do.
@settings(suppress_health_check=list(HealthCheck), max_examples=200, deadline=None)
def test_pin_pattern_length_random(data):
    pattern = data.draw(st.text(min_size=1).filter(is_valid_regex))
    length = data.draw(st.integers(min_value=0, max_value=40))
    pinned = pin_pattern_length(pattern, length, length)
    assume(pinned != pattern)
    value = data.draw(st.from_regex(pinned, fullmatch=True, alphabet=st.characters(codec=None)))
    assert len(value) == length
    assert re.fullmatch(pattern, value), f"{value!r} does not match {pattern}"


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        ("}?", 0, 0, ""),
        ("^()+$", None, 4096, "^$"),
        ("^[Ѐ-ӿ]+$", None, 4096, "^[Ѐ-ӿ]+$"),
        ("^(?:^)*a+$", None, 4096, "^a{1}$"),
        ("^(?:ab|abcd)+$", None, 4096, "^(?:ab|abcd)+$"),
    ],
)
def test_pin_pattern_length_rewrite(pattern, min_length, max_length, expected):
    assert pin_pattern_length(pattern, min_length, max_length) == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (
            r"^(?=.*[a-c])(?!.*[@#])[a-z0-9]{3,8}\Z",
            CharacterRules(required=("[a-c]",), forbidden=("[@\\#]",), body="[a-z0-9]", min_size=3, max_size=8),
        ),
        (r"^(?=.*a)[a-z]+\Z", CharacterRules(required=("a",), forbidden=(), body="[a-z]", min_size=1, max_size=None)),
        # A lookahead that needs a character before the class is not an anywhere-in-the-value rule.
        (r"^(?=.+?[a-c])[a-z]{3,8}\Z", None),
    ],
    ids=["required-and-forbidden", "literal", "non-empty-prefix"],
)
def test_character_rules(pattern, expected):
    assert character_rules(pattern) == expected


def test_pin_pattern_length_leaves_possessive_repeats_alone():
    # A possessive run keeps what it took, so sharing its count with the next part would admit rejected strings.
    assert pin_pattern_length("^[a-z]++[a-z]$", 5, 5) == "^[a-z]++[a-z]$"


@pytest.mark.parametrize(
    ("pattern", "length"),
    [(IOTTWINMAKER_ARN, 1024), (FSX_ARN, 8), (FSX_ARN, 512)],
    ids=["anchors", "lookarounds-minimum", "lookarounds-maximum"],
)
def test_pin_pattern_length_declines_when_zero_width_checks_reject_the_pinned_shape(pattern, length):
    assert pin_pattern_length(pattern, length, length) == pattern


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (
            r"^[\p{L}]+([ '-][\p{L}]+){0,2}$",
            r"^[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+([ '-][a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+){0,2}$",
        ),
        (r"\p{L}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\p{N}+", r"[0-9]+"),
        (r"\P{L}", r"[^a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]"),
        (r"\p{Alpha}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\p{Digit}+", r"[0-9]+"),
        (r"\p{Alnum}+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F0-9]+"),
        (r"\p{Space}", r"[ \t\n\r\f\v]"),
        (r"\p{Z}", r"[ \t\n\r\f\v]"),
        (r"\pL+", r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"),
        (r"\pN+", r"[0-9]+"),
        (r"\PL", r"[^a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]"),
        (r"\PN", r"[^0-9]"),
        (r"^[\w\s\-\/\pL,.#;:()']+$", r"^[\w\s\-\/a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F,.#;:()']+$"),
        # `\p{X}` beside other class members inlines its raw contents instead of nesting brackets.
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
        # A script class covers part of the script, so its complement admits characters the validator counts in it.
        (r"[\P{Greek}_]", None),
        (r"[\P{Cyrillic}_]", None),
        (r"[\P{Katakana}_]", None),
        (r"[\P{Hangul}_]", None),
        (r"[\P{Han}_]", None),
        (r"[\P{Hiragana}_]", None),
        (r"[\p{Tibetan}_]+", None),
        # Negated POSIX classes `[:^X:]` and unknown POSIX names bail out.
        (r"[[:^alnum:]_]", None),
        (r"[[:greek:]_]", None),
        # Brace hex naming no digits, or a codepoint past the last one.
        (r"[a\x{}b]", None),
        (r"[a\x{110000}]", None),
        # Neither spelling of a group name is read by every engine, so the name goes.
        (r"^(?<major>\d+)\.(?<minor>\d+)$", r"^(\d+)\.(\d+)$"),
        (r"(?<word>\w+)-(?<rest>.*)", r"(\w+)-(.*)"),
        (r"^(?P<major>\d+)\.(?P<minor>\d+)$", r"^(\d+)\.(\d+)$"),
        # Lookbehind opens the same way but names no group.
        (r"(?<=a)b", None),
        (r"(?<!a)b", None),
        # An escaped paren, and a class, leave the sequence as literal characters.
        (r"\(?<a>", None),
        (r"[(?<a>]", None),
        # A name Python cannot carry, and a name reused across alternatives, both readable once dropped.
        (r"(?<1st>a)", r"(a)"),
        (r"(?<x>a)|(?<x>b)", r"(a)|(b)"),
        # A hyphen beside a class escape cannot form a range, so ECMA reads it literally.
        (r"^/[\w-/]*[a-zA-Z0-9]$", r"^/[\w\-/]*[a-zA-Z0-9]$"),
        (r"[a-\d]", r"[a\-\d]"),
        (r"[\s-\S]", r"[\s\-\S]"),
        # The atom after such a hyphen closes the would-be range, so the next hyphen is literal too.
        (r"^[\d-a-z]+$", r"^[\d\-a\-z]+$"),
        (r"[\w-\x41-z]", r"[\w\-\x41\-z]"),
        (r"[\d-a-]", r"[\d\-a-]"),
        (r"[\d-a-b-c]", r"[\d\-a\-b-c]"),
        (r"[a-\d-b-q]", r"[a\-\d\-b-q]"),
        (r"\w-/", None),
        (r"[\w-]", None),
        # Already valid Python regex.
        (r"[a-z]+", None),
        (r"^\d+$", None),
        # No translation exists.
        (r"\p{Tibetan}", None),
        (r"\p{Script=Latin}", None),
    ],
)
def test_normalize_regex(pattern, expected):
    assert normalize_regex(pattern) == expected
    if expected:
        # A "Possible nested set" FutureWarning means the translation left nested brackets.
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
    # `||` names a literal `|` in the engine behind validation.
    (r"^[\p{L}||\p{N}]$", "a1|", "!"),
    (r"^[\p{L}||\p{M}||\p{Z}||\p{S}||\p{N}||\p{P}]$", "a1 .$|", "\x01"),
    (r"^[\p{Print}&&[^|:/]]$", ("a]", "&]", "[]", "^]", "|]", ":]", "/]"), ("a", "1", "]")),
    (r"^[[\p{L}]\p{N}]$", ("a1]", "[1]", "Z0]"), ("a]", "1]", "a1", "[]")),
    (r"^[\p{L}~~\p{N}]$", "a1~", "!"),
    (r"^[\p{L}\x{10FFFF}~~[a]]$", ("a]", "b]", "~]", "[]", "\U0010ffff]"), ("a", "1]", "\U0010ffff")),
    (r"^[a\x{60}]$", "a`", "b"),
    (r"^([$\-._+!*\x{60}(),;/?:@=&\w]|%([0-9a-fA-F?]{2}|[0-9a-fA-F?]?[*]))+$", "a`$_", " \t"),
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
    for value in matching:
        assert compiled.fullmatch(value), value
        assert validator.is_valid(value), value
    for value in rejected:
        assert not compiled.fullmatch(value), value
        assert not validator.is_valid(value), value


_END_ANCHOR_AGREEMENT = [
    (r"^a$", ("a",), ("a\n",)),
    (r"(?m)^a$", ("a", "a\nb"), ("ab",)),
    (r"\\$", ("\\",), ("\\\n",)),
    (r"\$", ("$",), ("a",)),
    (r"[$]", ("$",), ("a",)),
    (r"[^$]", ("a",), ("$",)),
    (r"^[^]$]+$", ("ab",), ("a$", "a]")),
    (r"^[]$]+$", ("]$",), ("a", "]\n")),
]


@pytest.mark.parametrize(
    ("pattern", "matching", "rejected"), _END_ANCHOR_AGREEMENT, ids=[item[0] for item in _END_ANCHOR_AGREEMENT]
)
def test_end_anchor_agrees_with_the_validator(pattern, matching, rejected):
    compiled = compile_ecma_pattern(pattern)
    assert compiled is not None
    validator = jsonschema_rs.validator_for({"type": "string", "pattern": pattern}, pattern_options=FANCY_REGEX_OPTIONS)
    for value in matching:
        assert compiled.search(value), value
        assert validator.is_valid(value), value
    for value in rejected:
        assert not compiled.search(value), value
        assert not validator.is_valid(value), value


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
# The violating draw is rare, so fewer examples would let a regression pass.
@settings(suppress_health_check=list(HealthCheck), max_examples=200)
def test_normalize_regex_never_produces_nested_classes(pattern: str) -> None:
    result = normalize_regex(pattern)
    if result is None:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        re.compile(result)


# CPython emits a FutureWarning for some bracket shapes while compiling; pattern validation must stay quiet.
@pytest.mark.parametrize(
    "pattern",
    ["^[[:a]b]+$", "[[:]]", "^a+$"],
    ids=["nested-set", "empty-nested-set", "plain"],
)
def test_is_valid_python_regex_does_not_warn(pattern: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assert is_valid_python_regex(pattern) is True


@given(st.data())
@settings(suppress_health_check=list(HealthCheck))
def test_update_quantifier_random(data):
    pattern = data.draw(st.text(min_size=1).filter(is_valid_regex))

    min_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())
    max_length = data.draw(st.integers(min_value=0, max_value=100) | st.none())

    assume(
        max_length is None
        or min_length is None
        or (min_length <= max_length and not (min_length is None and max_length is None))
    )

    modified_pattern = update_quantifier(pattern, min_length, max_length)

    assume(pattern != modified_pattern)

    assert is_valid_regex(modified_pattern)

    generated = data.draw(st.from_regex(modified_pattern, fullmatch=True, alphabet=st.characters(codec=None)))

    # Bounds the rewrite cannot fold stay with the schema, so only the pattern's own length claim must hold here.
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
        r"[^a]",
        ".",
        "^a",
        "a$",
        r"\ba\b",
        r"\Ba\B",
        r"\Aa",
        r"a\Z",
        "[abc]",
        "[a-z]",
        "[^abc]",
        r"[\d]",
        r"[a-z\d_]",
        "[a-zA-Z0-9]",
        f"[{chr(0x00)}-{chr(0x08)}]+",
        f"[{chr(0x200B)}]+",
        r"\d",
        r"\D",
        r"\w",
        r"\W",
        r"\s",
        r"\S",
        "a|b",
        "a|b|c",
        "(a)|(b)",
        "(abc)",
        "(?:abc)",
        "((a)(b))",
        "(?i:abc)",
        "(?-i:abc)",
        "(?im:abc)",
        "(?i-m:abc)",
        "a*",
        "a+",
        "a?",
        "a{3}",
        "a{2,5}",
        "a{2,}",
        "(ab)*",
        "(ab)+",
        "[a-z]*",
        "a*?",
        "a+?",
        "a??",
        "a{2,5}?",
        "^[a-z]+$",
        "abc",
        r"^[A-Z]{2}\d{3}-[a-z]+$",
        "(a|b)+",
        r"((\d{2})+)",
        r"(?=abc)",
        r"(?!abc)",
        r"(?<=abc)",
        r"(?<!abc)",
        r"\w+(?<!\s)",
        r"(?=\d)\w+",
        r"(foo)\1",
        r"([a-z])\1+",
        r"(x)(?(1)a|b)",
        r"(x)?(?(1)a|b)",
        r"(x)?(?(1)a)",
        "(?>abc)",
        "(?>\\d+)",
    ],
)
def test_serialize_roundtrip(pattern):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    re.compile(serialized)

    assert _serialize(list(sre_parse.parse(serialized))) == serialized

    for s in ["", "a", "abc", "ABC", "123", "a-b", "\t\n", "\x00", "xa", "b"]:
        assert bool(re.search(pattern, s)) == bool(re.search(serialized, s))


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("a*+", "a{0,}+"),
        ("a++", "a{1,}+"),
        ("a{2,5}+", "a{2,5}+"),
    ],
)
def test_serialize_possessive(pattern, expected):
    assert _serialize(list(sre_parse.parse(pattern))) == expected


@pytest.mark.parametrize(
    "char",
    [
        *string.printable,
        "\x00",
        "\x01",
        "\x07",
        "\x08",
        "\x1b",
        "\x7f",
        "\x80",
        "\xff",
        "\u00e9",
        "\u00f1",
        "\u0100",
        "\u4e2d",
        "\U0001f600",
    ],
    ids=[
        *string.printable,
        "null",
        "SOH",
        "BEL",
        "BS",
        "ESC",
        "DEL",
        "0x80",
        "0xff",
        "e-acute",
        "n-tilde",
        "A-macron",
        "CJK",
        "emoji",
    ],
)
def test_serialize_char(char):
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
        (r"\{x\}", "{x}"),
        (r"a\\b", "a\\b"),
        (r"\^\$", "^$"),
    ],
)
def test_serialize_escaped_literal(pattern, should_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    assert re.compile(serialized).fullmatch(should_match)
    assert jsonschema_rs.validator_for({"format": "regex"}, validate_formats=True).is_valid(serialized), serialized


@given(pattern=st.text(min_size=1, max_size=80).filter(is_valid_regex))
# Serialization bugs hide in rare random patterns, so the sweep needs breadth.
@settings(max_examples=500, suppress_health_check=list(HealthCheck), deadline=None)
def test_serialize_random_pattern(pattern):
    parsed = sre_parse.parse(pattern)
    try:
        serialized = _serialize(list(parsed))
    except InternalError:
        return

    re.compile(serialized)
    assert _serialize(list(sre_parse.parse(serialized))) == serialized


@pytest.mark.parametrize(
    ("pattern", "chars", "expected"),
    [
        ("arn:aws:[a-z]+/[a-z]+", "/", True),
        ("arn:aws:[a-z]+/[a-z]+/[0-9]+", "/", True),
        # A class member is never required.
        ("[a-z/]+", "/", False),
        ("^[a-zA-Z0-9_-]+$", "/", False),
        # Required only when every branch contains it.
        ("(foo/bar|baz/qux)", "/", True),
        ("(foo/bar|baz)", "/", False),
        ("[a-z]+/[0-9]+", "/", True),
        ("^arn:aws:appsync:[A-Za-z0-9_/.-]+/apis/[0-9A-Za-z_-]+$", "/", True),
        # A quantified group requires its contents only when it must repeat at least once.
        ("(a/b)*", "/", False),
        ("(a/b)+", "/", True),
        ("(a/b)?", "/", False),
        ("(a/b){2}", "/", True),
        ("(a/b){0,3}", "/", False),
        ("((foo/bar|baz/qux))+", "/", True),
        ("((foo/bar|baz))+", "/", False),
        ("prefix{suffix", "{", True),
        ("prefix}suffix", "}", True),
        ("[{abc}]+", "{", False),
        ("", "/", False),
        ("/", "/", True),
        # An invalid pattern requires nothing; the caller handles it separately.
        (r"\P{C}*", "/", False),
        (
            "arn:aws:kinesisvideo:[a-z0-9-]+:[0-9]+:[a-z]+/[a-zA-Z0-9_.-]+/[0-9]+",
            "/",
            True,
        ),
        (
            "arn:(aws[a-zA-Z-]*)?:[a-z]+:([a-z]{2}((-gov)|(-iso(b?)))?-[a-z]+-\\d{1})?:(\\d{12})?:[a-zA-Z0-9-_/:.]+",
            "/",
            False,
        ),
        ("[a-zA-Z0-9/._-]+", "/", False),
        # With several characters, the pattern needs any one of them.
        ("arn:aws:[a-z]+/[a-z]+", "/{}", True),
        ("prefix{suffix", "/{}", True),
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
        ("[!-0]", ALNUM, False),
        ("[9-@]", ALNUM, False),
    ],
)
def test_pattern_requires_char_outside(pattern, allowed, expected):
    assert pattern_requires_char_outside(pattern, allowed) == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (".*", True),
        (r"[\S\s]*", True),
        (r"[\w\W]*", True),
        ("a*", True),
        ("(?:abc)*", True),
        ("a*b?", True),
        ("", True),
        (".+", False),
        ("^$", False),
        # `^.*$` rejects "a\nb": the anchors pin where the match can start.
        ("^.*$", False),
        ("[a-z]*x", False),
        ("(?=x).*", False),
        ("(?!x)a*", False),
        (r"(?:(a)\1)*", False),
        ("(a*)", False),
        ("[", False),
        ("(?:ab|cd)*", True),
        ("(?:ab|cd)x*", False),
        ("(^a)*", False),
        ("(?:^ab|cd)*", False),
    ],
)
def test_matches_every_string(pattern, expected):
    assert matches_every_string(pattern) is expected


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


def test_rewrite_keeps_the_upper_bound_finite_when_parts_cannot_be_pinned():
    # Optional alternations block an exact length, and an open-ended rewrite would aim generation far past the bound.
    pattern = r"^arn:aws(-cn|-us-gov)?:[a-z0-9-]*:[a-z0-9-]*:([0-9]{12})?:.+$"

    assert pattern_length_bounds(update_quantifier(pattern, 1000, 1000)) == (1000, 1000)


# Strings over `ab` up to a handful of characters can be enumerated, so the length walk's answers are checkable.
BRUTE_FORCE_PATTERNS = [
    r"^(?:ab|abab)+$",
    r"^(?:a|bb)+$",
    r"^(ab)+$",
    r"^a{2,3}b*$",
    r"^a*b?a*$",
    r"^(?:a(?:b)?){2}$",
    r"^(?=a)ab*$",
    r"^a(?:bb)*$",
    r"^(?:aa|b){1,3}$",
    r"^(a+)\1$",
    r"^(?>a+)b*$",
    r"^(?:ab)++$",
    r"^(?:^)*a+$",
    r"^(?:ab){3}$",
    r"^(?:aab){2,4}$",
    r"^(?:(a)\1)+$",
    r"^((?:ab|abab)+)$",
    r"^(?:(?:ab|abab)+|(?:ba|baba)+)$",
    r"^(?:b|(a)\1)$",
    r"^(?>a+)*$",
    r"a*(^ab)(b+$)a*",
    r"^a(?=b)(|b)a+$",
]


BRUTE_FORCE_ALPHABET = "ab"


BRUTE_FORCE_MAX_LENGTH = 9


SMALL_REGEX_BODIES = st.recursive(
    st.sampled_from(("a", "b", "[ab]", ".", "(?=a)", "(?=b)", "(?!a)", "^", "$", r"\b")),
    lambda children: st.one_of(
        children.map(lambda child: f"(?:{child})"),
        st.tuples(children, children).map(lambda parts: "".join(parts)),
        st.tuples(children, children).map(lambda parts: f"(?:{parts[0]}|{parts[1]})"),
        st.tuples(children, st.sampled_from(("*", "+", "?", "{1,2}"))).map(lambda parts: f"(?:{parts[0]}){parts[1]}"),
    ),
    max_leaves=5,
)


SMALL_REGEX_PATTERNS = st.tuples(st.sampled_from(("", "^")), SMALL_REGEX_BODIES, st.sampled_from(("", "$", r"\Z"))).map(
    lambda parts: "".join(parts)
)


def _strings_of_length(length):
    return ("".join(letters) for letters in product(BRUTE_FORCE_ALPHABET, repeat=length))


@pytest.mark.parametrize("pattern", BRUTE_FORCE_PATTERNS)
def test_unreachable_length_never_rules_out_a_length_the_pattern_matches(pattern):
    # The walk may keep a length nothing matches, but dropping a matching one loses coverage the schema asked for.
    compiled = re.compile(pattern)
    for length in range(BRUTE_FORCE_MAX_LENGTH):
        if any(compiled.fullmatch(text) for text in _strings_of_length(length)):
            assert not pattern_length_is_unreachable(pattern, length, length), (pattern, length)


@pytest.mark.parametrize("pattern", BRUTE_FORCE_PATTERNS)
def test_pinned_pattern_admits_only_original_matches_of_the_pinned_length(pattern):
    original = re.compile(pattern)
    for length in range(1, BRUTE_FORCE_MAX_LENGTH):
        pinned = pin_pattern_length(pattern, length, length)
        if pinned == pattern:
            continue
        compiled = re.compile(pinned)
        admitted = [
            text
            for size in range(BRUTE_FORCE_MAX_LENGTH)
            for text in _strings_of_length(size)
            if compiled.fullmatch(text)
        ]
        assert admitted, (pattern, length)
        for text in admitted:
            assert len(text) == length, (pattern, length, text)
            assert original.fullmatch(text), (pattern, length, text)


@example(pattern=r"^a(?=b)(|b)a+$", length=2)
@example(pattern=r"(?:(?=a))+", length=0)
@example(pattern=r"a(?:^)+", length=1)
@example(pattern=r"(?:(?!b))+.", length=1)
@settings(max_examples=50, suppress_health_check=list(HealthCheck), deadline=None)
@given(pattern=SMALL_REGEX_PATTERNS, length=st.integers(min_value=0, max_value=8))
def test_pinned_generated_pattern_is_a_non_empty_subset(pattern, length):
    pinned = pin_pattern_length(pattern, length, length)
    if pinned == pattern:
        return
    admitted = {
        text
        for size in range(BRUTE_FORCE_MAX_LENGTH + 1)
        for text in _strings_of_length(size)
        if re.fullmatch(pinned, text)
    }
    assert admitted, (pattern, length, pinned)
    assert {len(text) for text in admitted} == {length}
    assert all(re.fullmatch(pattern, text) for text in admitted)


@pytest.mark.parametrize("pattern", BRUTE_FORCE_PATTERNS)
def test_pin_leaves_a_length_alone_when_the_pattern_cannot_reach_it(pattern):
    compiled = re.compile(pattern)
    for length in range(1, BRUTE_FORCE_MAX_LENGTH):
        if not any(compiled.fullmatch(text) for text in _strings_of_length(length)):
            assert pin_pattern_length(pattern, length, length) == pattern, (pattern, length)


@pytest.mark.parametrize(
    ("min_length", "max_length"),
    [(6, 4), (10, 0)],
    ids=["min-above-max", "max-zero"],
)
def test_contradictory_length_window_is_unreachable(min_length, max_length):
    assert pattern_length_is_unreachable(r"^[a-z]+$", min_length, max_length) is True


@pytest.mark.parametrize("pattern", ["[", "a{2,1}", "(?P<>a)"], ids=["open-class", "reversed-bounds", "empty-name"])
def test_unreadable_pattern_rules_nothing_out(pattern):
    # A pattern Python cannot read says nothing about length, so generation keeps its own path.
    assert pattern_length_is_unreachable(pattern, 1, 5) is False
    assert pin_pattern_length(pattern, 1, 5) == pattern
    assert pattern_length_bounds(pattern) == (0, None)
