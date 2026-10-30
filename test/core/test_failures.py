from pathlib import Path

import pytest

from schemathesis.config import OutputConfig
from schemathesis.core.failures import Failure, ServerError, Severity, format_failures, get_origin


def origin_of_assertion_at(path: Path) -> tuple:
    code = compile("raise AssertionError('boom')", str(path), "exec")
    try:
        exec(code, {})
    except AssertionError as exc:
        return get_origin(exc)
    raise RuntimeError("unreachable")


def test_origin_is_the_same_in_two_checkouts(tmp_path, monkeypatch):
    # Origins end up in stored baseline entries, so the checkout root must not be part of them.
    origins = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        (root / "tests").mkdir(parents=True)
        monkeypatch.chdir(root)
        origins.append(origin_of_assertion_at(root / "tests" / "checks.py"))

    assert origins[0] == origins[1]


def test_failure_repr_shows_its_fields():
    failure = Failure(operation="GET /x", title="Title", message="Message", case_id="abc", severity=Severity.HIGH)

    assert repr(failure) == (
        "Failure(operation='GET /x', title='Title', message='Message', case_id='abc', severity=<Severity.HIGH: 'high'>)"
    )


@pytest.mark.parametrize(
    ("status_line", "expected"),
    [
        (None, "\n- ST101 Server error\n\n[500] Internal Server Error:\n\n    <EMPTY>"),
        ("CUSTOM: upgrade your client.", "\n- ST101 Server error\n\nCUSTOM: upgrade your client.\n\n    <EMPTY>"),
        ("CUSTOM: file\x00exists", "\n- ST101 Server error\n\nCUSTOM: file\\x00exists\n\n    <EMPTY>"),
    ],
    ids=["default", "custom", "control-characters"],
)
def test_status_line_from_response(response_factory, status_line, expected):
    response = response_factory.core(status_code=500, content=b"", content_type=None, status_line=status_line)
    failures = [ServerError(operation="GET /x", status_code=500)]
    assert (
        format_failures(case_id=None, response=response, failures=failures, curl=None, config=OutputConfig())
        == expected
    )


def test_truncated_response_keeps_status_line(response_factory):
    response = response_factory.core(content=b"0123456789", status_line="CUSTOM: went wrong")
    assert response.truncated(4).status_line == "CUSTOM: went wrong"
