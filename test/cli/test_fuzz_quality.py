from pathlib import Path


def test_fuzz_cli_suite_has_no_monkeypatch_test_arguments() -> None:
    text = Path("test/cli/test_fuzz.py").read_text(encoding="utf-8")
    assert "monkeypatch" not in text
