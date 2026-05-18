import os

import pytest

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.config._dictionaries import coerce_entries_for_type, parse_body_path
from schemathesis.config._generation import GenerationConfig


def test_from_file_loaded_with_relative_path_and_escapes(tmp_path):
    (tmp_path / "edge.dict").write_text(
        '# header comment\n"admin"\n"root"\ncrlf="\\r\\n"\nnul="\\x00"\n', encoding="utf-8"
    )
    (tmp_path / "schemathesis.toml").write_text(
        """
[dictionaries.edge]
from-file = "edge.dict"
""",
        encoding="utf-8",
    )
    definition = SchemathesisConfig.from_path(tmp_path / "schemathesis.toml").dictionaries["edge"]
    assert [e.value for e in definition.entries] == ["admin", "root", "\r\n", "\x00"]
    assert definition.source_kind == "from-file"
    assert definition.source_path == str((tmp_path / "edge.dict").resolve())


def test_from_file_parse_error_reports_line_number(tmp_path):
    (tmp_path / "bad.dict").write_text('"ok"\nnot-quoted\n', encoding="utf-8")
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.bad]\nfrom-file = "bad.dict"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="line 2: entry must be a quoted string"):
        SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")


def test_from_file_empty_is_error(tmp_path):
    (tmp_path / "empty.dict").write_text("# only comments\n", encoding="utf-8")
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.empty]\nfrom-file = "empty.dict"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="contains no entries"):
        SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")


def test_file_dictionary_filters_non_finite_numbers(tmp_path):
    (tmp_path / "n.dict").write_text('"NaN"\n"Infinity"\n"3.14"\n', encoding="utf-8")
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.n]\nfrom-file = "n.dict"\n', encoding="utf-8")
    config = SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")
    assert coerce_entries_for_type(config.dictionaries["n"].entries, "number") == ((2, 3.14),)


def test_operation_type_wide_dictionary_merges_per_key_with_project():
    config = SchemathesisConfig.from_dict(
        {
            "dictionaries": {
                "s": {"values": ["X"]},
                "i": {"values": [1]},
            },
            "generation": {"dictionaries": {"string": {"dictionary": "s", "probability": 0.1}}},
            "operations": [
                {
                    "include-tag": "search",
                    "generation": {"dictionaries": {"integer": {"dictionary": "i", "probability": 0.3}}},
                }
            ],
        }
    )
    merged = GenerationConfig.from_hierarchy(
        [
            config.projects.default.operations.operations[0].generation,
            config.projects.default.generation,
        ]
    )
    assert set(merged.dictionaries) == {"string", "integer"}


def test_provenance_index_uses_original_position_after_type_filter():
    config = SchemathesisConfig.from_dict({"dictionaries": {"mixed": {"values": ["bad", "1", "42"]}}})
    assert coerce_entries_for_type(config.dictionaries["mixed"].entries, "integer") == ((1, 1), (2, 42))


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_inline_non_finite_value_rejected_by_schema(value):
    with pytest.raises(ConfigError, match="Must be one of: string or integer or number"):
        SchemathesisConfig.from_dict({"dictionaries": {"x": {"values": [value]}}})


def test_absolute_dictionary_file_path(tmp_path):
    dict_file = tmp_path / "abs.dict"
    dict_file.write_text('"ok"\n', encoding="utf-8")
    # TOML literal (single-quoted) string skips escape processing so the Windows
    # path's backslashes don't get parsed as `\U` / `\a` sequences.
    (tmp_path / "schemathesis.toml").write_text(f"[dictionaries.x]\nfrom-file = '{dict_file}'\n", encoding="utf-8")
    config = SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")
    assert config.dictionaries["x"].source_path == str(dict_file)


def test_missing_dictionary_file_reports_error(tmp_path):
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.x]\nfrom-file = "missing.dict"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="Dictionary file `missing.dict` not found"):
        SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")


def test_unreadable_dictionary_file_reports_error(tmp_path):
    denied = tmp_path / "denied.dict"
    denied.write_text('"ok"\n', encoding="utf-8")
    denied.chmod(0)
    try:
        if os.access(denied, os.R_OK):
            pytest.skip("filesystem ignored chmod 0 (likely running as root)")
        (tmp_path / "schemathesis.toml").write_text('[dictionaries.x]\nfrom-file = "denied.dict"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="is not readable"):
            SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")
    finally:
        denied.chmod(0o644)


@pytest.mark.parametrize(
    "content,match",
    [
        ('"a\\"', "trailing backslash"),
        ('"a\\x"', r"truncated \\x escape"),
        ('"\\xZZ"', r"invalid hex escape `\\xZZ`"),
        ('"\\q"', r"unknown escape `\\q`"),
    ],
    ids=["trailing-backslash", "truncated-hex", "invalid-hex", "unknown-escape"],
)
def test_libfuzzer_escape_errors(tmp_path, content, match):
    (tmp_path / "bad.dict").write_text(content + "\n", encoding="utf-8")
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.x]\nfrom-file = "bad.dict"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")


@pytest.mark.parametrize(
    "content,match",
    [
        ('="value"', "invalid entry name ``"),
        ('foo+bar="x"', "invalid entry name `foo\\+bar`"),
    ],
    ids=["empty-prefix", "non-alphanumeric"],
)
def test_libfuzzer_invalid_entry_name(tmp_path, content, match):
    (tmp_path / "bad.dict").write_text(content + "\n", encoding="utf-8")
    (tmp_path / "schemathesis.toml").write_text('[dictionaries.x]\nfrom-file = "bad.dict"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        SchemathesisConfig.from_path(tmp_path / "schemathesis.toml")


@pytest.mark.parametrize(
    "values,ty,expected",
    [
        ([42, 3.14], "string", ((0, "42"), (1, "3.14"))),
        ([42, "10"], "number", ((0, 42), (1, 10.0))),
        ([3.14, 42], "integer", ((1, 42),)),
        (["", "  ", "5"], "integer", ((2, 5),)),
        (["", "  ", "1.5"], "number", ((2, 1.5),)),
        (["abc"], "number", ()),
    ],
    ids=[
        "int-and-float-to-string",
        "int-and-numeric-string-to-number",
        "float-skipped-for-integer",
        "empty-and-whitespace-skipped-for-integer",
        "empty-and-whitespace-skipped-for-number",
        "non-numeric-string-skipped-for-number",
    ],
)
def test_coerce_entries_for_type_edges(values, ty, expected):
    config = SchemathesisConfig.from_dict({"dictionaries": {"x": {"values": values}}})
    assert coerce_entries_for_type(config.dictionaries["x"].entries, ty) == expected


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("body.x", "/x"),
        ("body.user.email", "/user/email"),
        ("body.items[*].name", "/items/*/name"),
        ("body.tags[*]", "/tags/*"),
        ("body.[*]", "/*"),
        ("body.deeply.nested.field", "/deeply/nested/field"),
        ("body.with-dash.under_score", "/with-dash/under_score"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_parse_body_path_valid(expr, expected):
    assert parse_body_path(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "body.",
        "body..x",
        "body.x..y",
        "body.x[3]",
        "body.x[a]",
        "body.x[*][*]",
        "body.x[**]",
        "body.@invalid",
        "body.@bad[*]",
        "body.[a]",
        "body.x.",
        "not_body.x",
    ],
    ids=lambda v: v,
)
def test_parse_body_path_rejects(expr):
    with pytest.raises(ConfigError):
        parse_body_path(expr)
