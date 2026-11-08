from __future__ import annotations

import os
from typing import Any

from test.utils import allure_results

# Hypothesis renamed this header in 6.159.0 ("Falsifying example" -> "Failing test case"). Tests that
# assert the header is *present* fail loudly on a future rename, which is what keeps the tests asserting
# its absence from silently passing against a string Hypothesis no longer emits.
HYPOTHESIS_FAILURE_HEADERS = ("Failing test case", "Falsifying example")


def has_hypothesis_failure_header(text: str, test_name: str = "") -> bool:
    return any(f"{header}: {test_name}" in text for header in HYPOTHESIS_FAILURE_HEADERS)


def allure_outcomes(allure_dir: str | os.PathLike) -> dict[str, tuple[str, str]]:
    outcomes = {}
    for result in allure_results(allure_dir):
        raw_message = result.get("statusDetails", {}).get("message", "")
        message = raw_message.splitlines()[0] if raw_message else ""
        if message.startswith("ConnectionError: "):
            message = "ConnectionError"
        outcomes[result["name"]] = (result["status"], message)
    return outcomes


def make_pytest_outcome_test(testdir: Any, ctx: Any, outcome: str, report_config: str, *, lazy: bool = False) -> str:
    """Write a one-operation pytest test that ends with `outcome` and return its operation label."""
    marker = ""
    config = ""
    if outcome == "check_failure":
        api = ctx.openapi.apps.failure()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        body = "case.call_and_validate()"
        label = "GET /api/failure"
    elif outcome == "unsatisfiable":
        api = ctx.openapi.apps.unsatisfiable()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        config = "schema.config.generation.update(modes=[GenerationMode.POSITIVE])"
        body = "case.call_and_validate()"
        label = "POST /api/unsatisfiable"
    elif outcome == "network_error":
        schema_dict = ctx.openapi.build_schema({"/network": {"get": {"responses": {"200": {"description": "OK"}}}}})
        schema = f"schemathesis.openapi.from_dict({schema_dict!r})"
        config = 'schema.config.update(base_url="http://127.0.0.1:1")'
        body = "case.call()"
        label = "GET /network"
    elif outcome == "schema_error":
        schema_dict = ctx.openapi.build_schema(
            {
                "/invalid": {
                    "get": {
                        "parameters": [{"$ref": "#/components/parameters/Missing"}],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
        schema = f"schemathesis.openapi.from_dict({schema_dict!r})"
        body = "case.call()"
        label = "GET /invalid"
    else:
        api = ctx.openapi.apps.success()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        body = {
            "assertion": 'case.call(); assert False, "boom"',
            "runtime_error": 'case.call(); raise RuntimeError("bug")',
            "skip": 'pytest.skip("why")',
            "mark_skip": "case.call()",
        }[outcome]
        if outcome == "mark_skip":
            marker = '@pytest.mark.skip(reason="why")'
        label = "GET /api/success"
    if lazy:
        setup = "\n    ".join(line for line in (f"schema = {schema}", config, report_config, "return schema") if line)
        source = f"""
@pytest.fixture
def api_schema():
    {setup}

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

{marker}
@lazy_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    {body}
"""
    else:
        source = f"""
schema = {schema}
{config}
{report_config}

{marker}
@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    {body}
"""
    testdir.make_test(source)
    return label
