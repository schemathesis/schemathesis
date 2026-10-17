import pytest

from schemathesis.checks import CHECKS
from schemathesis.cli.commands.explain.explanations import EXPLANATIONS
from schemathesis.failure_codes import FAILURE_CLASSES


def test_every_code_has_an_explanation():
    assert set(EXPLANATIONS) == {cls.code for cls in FAILURE_CLASSES}


@pytest.mark.parametrize("cls", FAILURE_CLASSES, ids=lambda cls: cls.code)
def test_failure_class_names_existing_checks(cls):
    known = set(CHECKS.get_all_names()) | {"max_response_time"}
    assert set(cls.checks) <= known
    tuning = EXPLANATIONS[cls.code].tuning
    assert tuning is None or tuning[0] in cls.checks


@pytest.mark.parametrize("argument", ["st108", "108", " St108 "])
def test_explain_accepts_code_aliases(cli, argument):
    assert cli.main("explain", argument).stdout == cli.main("explain", "ST108").stdout


@pytest.mark.snapshot(replace=False)
@pytest.mark.parametrize(
    ("args", "columns"),
    [
        *(pytest.param((code,), None, id=code) for code in sorted(EXPLANATIONS)),
        pytest.param(("--list",), None, id="list"),
        pytest.param(("--list",), "120", id="list-wide"),
        pytest.param(("response_schema_conformance",), None, id="check-name"),
        *(
            pytest.param((argument,), None, id=f"unknown-{argument}")
            for argument in ["ST1O8", "999", "nonsense", "response-schema"]
        ),
        pytest.param((), None, id="no-argument"),
        pytest.param(("ST108", "--force-color"), None, id="force-color"),
        pytest.param(("ST108",), "40", id="narrow"),
    ],
)
def test_explain_output(cli, snapshot_cli, args, columns):
    assert cli.main("explain", *args, env={"COLUMNS": columns} if columns else None) == snapshot_cli
