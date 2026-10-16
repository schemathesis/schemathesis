import re
import textwrap
import tomllib
from pathlib import Path

import pytest

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.explain.docs import generate_docs_page
from schemathesis.cli.commands.explain.explanations import EXPLANATIONS
from schemathesis.cli.commands.explain.render import also_disables
from schemathesis.config import SchemathesisConfig
from schemathesis.config._phases import DEFAULT_UNEXPECTED_METHODS
from schemathesis.core.failures import FailureGroup, ResponseTimeExceeded
from schemathesis.failure_codes import iter_failure_classes

EXEMPT_FROM_EXECUTION = {
    "ST102": "needs a coverage-phase Content-Type probe",
    "ST112": "needs a measured response time",
    "ST201": "needs generated negative data",
    "ST202": "needs generated positive data",
    "ST203": "needs a generated missing-header case",
    "ST204": "needs an unsupported-method probe",
    "ST205": "needs an OPTIONS probe",
    "ST301": "needs stateful history",
    "ST302": "needs stateful history",
    "ST401": "needs auth scenarios",
    "ST402": "needs two identities",
    "ST501": "GraphQL response",
    "ST502": "GraphQL response",
    "ST503": "GraphQL response",
    "ST504": "GraphQL response",
    "ST901": "user-defined check",
}
DOCS_PAGE = Path(__file__).parent.parent.parent / "docs" / "reference" / "failures.md"
PROSE_FIELDS = ("summary", "why", "how_found", "fix", "false_positives")


def test_every_code_has_an_explanation():
    assert set(EXPLANATIONS) == {cls.code for cls in iter_failure_classes()}


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_explanation_names_existing_checks(code):
    explanation = EXPLANATIONS[code]
    known = set(CHECKS.get_all_names()) | {"max_response_time"}
    assert set(explanation.checks) | set(explanation.tuning) | set(explanation.graphql_only) <= known
    assert set(explanation.tuning) | set(explanation.graphql_only) <= set(explanation.checks)


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_explanation_prose_holds_paragraphs_and_bullets_only(code):
    explanation = EXPLANATIONS[code]
    for field in ("summary", "why", "how_found", "fix"):
        assert getattr(explanation, field).strip(), field
    for field in PROSE_FIELDS:
        for line in getattr(explanation, field).splitlines():
            assert not line.lstrip().startswith(("#", "```")), (field, line)


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_explanation_has_example_or_scenario(code):
    explanation = EXPLANATIONS[code]
    if code in EXEMPT_FROM_EXECUTION:
        assert explanation.example is None
    else:
        assert explanation.example is not None
        assert explanation.scenario == ""


@pytest.mark.parametrize("code", sorted(set(EXPLANATIONS) - set(EXEMPT_FROM_EXECUTION)))
def test_example_raises_only_its_own_code(code, response_factory):
    explanation = EXPLANATIONS[code]
    example = explanation.example
    schema = schemathesis.openapi.from_dict(example.schema)
    operation = next(result.ok() for result in schema.get_all_operations())
    headers = dict(example.headers)
    content_type = headers.pop("Content-Type", None)
    response = response_factory.requests(
        status_code=example.status_code,
        headers=headers,
        content=example.body.encode(),
        content_type=content_type,
        method=operation.method.upper(),
    )
    checks = [check for check in CHECKS.get_all() if check.__name__ in explanation.checks]

    with pytest.raises(FailureGroup) as exc_info:
        operation.Case().validate_response(response, checks=checks)

    assert {failure.code for failure in exc_info.value.exceptions} == {code}


@pytest.mark.snapshot(replace=False)
def test_explain_code(cli, snapshot_cli):
    assert cli.main("explain", "ST108") == snapshot_cli


@pytest.mark.parametrize(
    "argument", ["st108", "108", " St108 ", "response-schema", "JsonSchemaError", "jsonschemaerror"]
)
def test_explain_accepts_code_aliases(cli, argument):
    assert cli.main("explain", argument).stdout == cli.main("explain", "ST108").stdout


@pytest.mark.snapshot(replace=False)
def test_explain_check_name(cli, snapshot_cli):
    assert cli.main("explain", "response_schema_conformance") == snapshot_cli


@pytest.mark.snapshot(replace=False)
def test_explain_list(cli, snapshot_cli):
    assert cli.main("explain", "--list") == snapshot_cli


@pytest.mark.snapshot(replace=False)
def test_explain_list_wide_terminal(cli, snapshot_cli):
    assert cli.main("explain", "--list", env={"COLUMNS": "160"}) == snapshot_cli


@pytest.mark.snapshot(replace=False)
@pytest.mark.parametrize("argument", ["ST1O8", "999", "nonsense"])
def test_explain_unknown_input(cli, snapshot_cli, argument):
    assert cli.main("explain", argument) == snapshot_cli


@pytest.mark.snapshot(replace=False)
def test_explain_without_argument(cli, snapshot_cli):
    assert cli.main("explain") == snapshot_cli


@pytest.mark.snapshot(replace=False)
def test_explain_force_color(cli, snapshot_cli):
    assert cli.main("explain", "ST108", "--force-color") == snapshot_cli


@pytest.mark.snapshot(replace=False)
def test_explain_narrow_terminal(cli, snapshot_cli):
    assert cli.main("explain", "ST108", env={"COLUMNS": "40"}) == snapshot_cli


@pytest.mark.snapshot(replace=False)
@pytest.mark.parametrize("code", ["ST112", "ST201", "ST504", "ST901"])
def test_explain_generated_sections(cli, snapshot_cli, code):
    assert cli.main("explain", code) == snapshot_cli


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_explain_every_code(cli, code):
    result = cli.main("explain", code)
    assert result.exit_code == 0
    assert result.stdout.startswith(code)


def test_docs_page_is_up_to_date():
    assert generate_docs_page() == DOCS_PAGE.read_text(encoding="utf-8")


def test_docs_page_has_an_anchor_per_code():
    anchors = re.findall(r"\{#(st\d{3})\}", generate_docs_page())
    assert anchors == [code.lower() for code in sorted(EXPLANATIONS)]


def test_docs_page_config_snippets_are_valid():
    snippets = re.findall(r"```toml\n(.*?)```", generate_docs_page(), re.DOTALL)
    assert snippets
    for snippet in snippets:
        SchemathesisConfig.from_dict(tomllib.loads(snippet))


def test_verify_override_only_where_replay_gives_no_verdict():
    # `st replay` judges every stored case except timing failures; custom checks need their hooks loaded to rerun.
    not_replayable = {cls.code for cls in iter_failure_classes() if issubclass(cls, ResponseTimeExceeded)}
    assert {code for code, explanation in EXPLANATIONS.items() if explanation.verify} == not_replayable | {"ST901"}


def test_also_disables_marks_codes_raised_only_for_graphql():
    assert also_disables("ST101", ("not_a_server_error",)) == [
        ("ST102", "Server error on unexpected Content-Type"),
        ("ST109", "JSON deserialization error (GraphQL only)"),
        ("ST201", "API accepted schema-violating request (GraphQL only)"),
        ("ST501", "GraphQL server error"),
        ("ST502", "GraphQL schema violation"),
        ("ST503", "GraphQL client error"),
        ("ST504", "Unexpected GraphQL Response"),
    ]


def test_unsupported_method_page_lists_the_default_methods():
    listed = re.findall(r"`(\w+)`", EXPLANATIONS["ST204"].how_found.split("The default list is", 1)[1].split(".")[0])
    assert set(listed) == DEFAULT_UNEXPECTED_METHODS


@pytest.mark.parametrize("code", sorted(EXPLANATIONS))
def test_scenario_failure_headlines_carry_their_code(code):
    # Run output prints `- ST108 Response violates schema`; scenarios quote it.
    headlines = re.findall(r"^  - (.*)$", textwrap.dedent(EXPLANATIONS[code].scenario), re.MULTILINE)
    assert [line for line in headlines if not line.startswith(f"{code} ")] == []
