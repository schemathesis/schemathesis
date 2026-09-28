import pytest

from schemathesis.config import ConfigError, SchemathesisConfig


def load_expected_statuses(codes):
    config = SchemathesisConfig.from_dict({"checks": {"not_a_server_error": {"expected-statuses": codes}}})
    return config.projects.default.checks.not_a_server_error.expected_statuses


@pytest.mark.parametrize(
    "codes",
    [["200", "404"], ["2xx", "4xx"], ["200", "2xx", "404", "4xx"], ["2X1", "21X"], [200, 404], []],
    ids=["exact", "wildcards", "mixed", "single-wildcard-digit", "integers", "empty"],
)
def test_valid_expected_statuses(codes):
    assert load_expected_statuses(codes) == [str(code) for code in codes]


@pytest.mark.parametrize(
    ("codes", "invalid"),
    [
        (["200", "600"], "600"),
        (["2xx", "6xx"], "6xx"),
        (["2xx", "xxx"], "xxx"),
        (["2xx", "999"], "999"),
        (["200", "abc"], "abc"),
        (["200", "2bc"], "2bc"),
        (["200", "2Xc"], "2Xc"),
        (["200", "20"], "20"),
        (["200", "2xxx"], "2xxx"),
        (["200", "xx"], "xx"),
        (["600", "abc", "200"], "600, abc"),
    ],
)
def test_invalid_expected_statuses(codes, invalid):
    with pytest.raises(ConfigError) as exc:
        load_expected_statuses(codes)
    assert str(exc.value) == (
        f"Invalid status code(s): {invalid}. Use valid 3-digit codes between 100 and 599, "
        "or wildcards (e.g., 2XX, 2X0, 20X), where X is a wildcard digit."
    )
