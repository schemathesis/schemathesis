import pytest

from schemathesis.generation.jsonschema.context import Alphabet, StrategyContext


@pytest.mark.parametrize(
    ("allow_x00", "codec", "name", "expected"),
    [
        (True, "utf-8", "abc", True),
        (True, "utf-8", "a\x00b", True),
        (False, "utf-8", "a\x00b", False),
        (True, "utf-8", "\ud800", False),
        (True, "ascii", "café", False),
        (True, "ascii", "abc", True),
        (True, None, "café", True),
    ],
)
def test_alphabet_rejects_disallowed_names(allow_x00, codec, name, expected):
    assert Alphabet(allow_x00=allow_x00, codec=codec).check_name_allowed(name) is expected


def test_strategy_context_wires_a_usable_registry_and_alphabet():
    context = StrategyContext()
    assert context.formats.get("date-time") is not None
    assert context.alphabet.check_name_allowed("abc") is True
