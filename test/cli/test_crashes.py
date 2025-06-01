from unittest import mock

import pytest
from hypothesis import HealthCheck, Phase, Verbosity, example, given, settings
from hypothesis import strategies as st
from hypothesis.provisional import urls
from hypothesis_jsonschema import from_schema
from requests import Response

from schemathesis import GenerationMode
from schemathesis.checks import CHECKS
from schemathesis.cli.commands.run.handlers.output import DEFAULT_INTERNAL_ERROR_MESSAGE
from schemathesis.config._validator import CONFIG_SCHEMA
from schemathesis.core.transforms import deepclone
from schemathesis.generation.metrics import METRICS


@pytest.fixture(scope="module")
def mocked_schema():
    """Module-level mock for fast hypothesis tests.

    We're checking the input validation part, what comes from the network is not important in this context,
    the faster run will be, the better.
    """
    response = Response()
    response._content = b"""openapi: 3.0.0
info:
  title: Sample API
  description: API description in Markdown.
  version: 1.0.0
paths: {}
servers:
  - url: https://api.example.com/{basePath}
    variables:
      basePath:
        default: v1
"""
    response.status_code = 200
    with mock.patch("requests.sessions.Session.send", return_value=response):
        yield


@pytest.fixture(scope="module")
def schema_url(server):
    # In this module we don't care about resetting the app or testing different Open API versions
    # Only whether Schemathesis crashes on allowed input
    return f"http://127.0.0.1:{server['port']}/schema.yaml"


@st.composite
def delimited(draw):
    key = draw(st.text(min_size=1, alphabet=st.characters(min_codepoint=1, max_codepoint=127))).strip() or "a"
    value = draw(st.text(min_size=1, alphabet=st.characters(min_codepoint=1, max_codepoint=127))).strip() or "b"
    return f"{key}:{value}"


@st.composite
def paths(draw):
    path = draw(st.text()).lstrip("/")
    return "/" + path


def csv_strategy(enum, exclude=()):
    return st.lists(st.sampled_from([item.name for item in enum if item.name not in exclude]), min_size=1).map(",".join)


# The following strategies generate CLI parameters, for example "--workers=5" or "--max-failures=10"
@settings(
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    phases=[Phase.explicit, Phase.reuse, Phase.target, Phase.generate],
    deadline=None,
)
@given(
    params=st.fixed_dictionaries(
        {},
        optional={
            "auth": delimited(),
            "generation-mode": st.sampled_from([item.name.lower() for item in GenerationMode] + ["all"]),
            "generation-optimize": st.sampled_from(METRICS.get_all_names()),
            "workers": st.integers(min_value=1, max_value=64),
            "request-timeout": st.integers(min_value=1),
            "max-response-time": st.integers(min_value=1),
            "generation-with-security-parameters": st.booleans(),
            "generation-database": st.text(),
            "generation-max-examples": st.integers(min_value=1),
            "generation-seed": st.integers(),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
    flags=st.fixed_dictionaries(
        {},
        optional={
            key: st.booleans()
            for key in (
                "generation-deterministic",
                "continue-on-failure",
                "generation-unique-inputs",
                "no-color",
            )
        },
    ).map(lambda flags: [f"--{flag}" for flag in flags]),
    multiple_params=st.fixed_dictionaries(
        {},
        optional={
            "checks": st.lists(st.sampled_from(CHECKS.get_all_names() + ["all"]), min_size=1),
            "header": st.lists(delimited(), min_size=1),
            "include-name": st.lists(st.text(min_size=1)),
            "exclude-name": st.lists(st.text(min_size=1)),
            "include-method": st.lists(st.text(min_size=1)),
            "exclude-method": st.lists(st.text(min_size=1)),
            "include-tag": st.lists(st.text(min_size=1)),
            "exclude-tag": st.lists(st.text(min_size=1)),
            "include-operation-id": st.lists(st.text(min_size=1)),
            "exclude-operation-id": st.lists(st.text(min_size=1)),
        },
    ).map(lambda params: [f"--{key}={value}" for key, values in params.items() for value in values]),
    csv_params=st.fixed_dictionaries(
        {},
        optional={
            "suppress-health-check": csv_strategy(
                HealthCheck, exclude=("function_scoped_fixture", "differing_executors")
            ),
            "hypothesis-phases": csv_strategy(Phase, exclude=("explain",)),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
)
@example(params=[], flags=[], multiple_params=["--header=0:0\r"], csv_params=[])
@example(params=["--max-examples=0"], flags=[], multiple_params=[], csv_params=[])
@pytest.mark.usefixtures("mocked_schema")
def test_valid_parameters_combos(cli, schema_url, params, flags, multiple_params, csv_params):
    result = cli.run(
        schema_url,
        *params,
        *multiple_params,
        *flags,
        *csv_params,
    )
    check_result(result)


@settings(
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    phases=[Phase.explicit, Phase.reuse, Phase.target, Phase.generate],
    deadline=None,
)
@given(
    params=st.fixed_dictionaries(
        {},
        optional={
            "auth": st.text(),
            "generation-mode": st.text(),
            "generation-optimize": st.text(),
            "workers": st.text(),
            "request-timeout": st.text(),
            "max-response-time": st.text(),
            "generation-with-security-parameters": st.booleans(),
            "generation-database": st.text(),
            "generation-max-examples": st.text(),
            "generation-seed": st.text(),
            "experimental": st.text(),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
    flags=st.fixed_dictionaries(
        {},
        optional={
            key: st.booleans()
            for key in (
                "generation-deterministic",
                "continue-on-failure",
                "generation-unique-inputs",
                "no-color",
            )
        },
    ).map(lambda flags: [f"--{flag}" for flag in flags]),
    multiple_params=st.fixed_dictionaries(
        {},
        optional={
            "checks": st.lists(st.text(), min_size=1),
            "header": st.lists(st.text(), min_size=1),
        },
    ).map(lambda params: [f"--{key}={value}" for key, values in params.items() for value in values]),
    csv_params=st.fixed_dictionaries(
        {},
        optional={
            "suppress-health-check": st.lists(st.text()).map(",".join),
            "hypothesis-phases": st.lists(st.text()).map(",".join),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
)
@example(params=["--checks=0"], flags=[], multiple_params=[], csv_params=[])
@example(params=["--generation-maximize=0"], flags=[], multiple_params=[], csv_params=[])
@example(params=["--exclude-operation-id=0", "--include-operation-id=0"], flags=[], multiple_params=[], csv_params=[])
@pytest.mark.usefixtures("mocked_schema")
def test_random_parameters_combos(cli, schema_url, params, flags, multiple_params, csv_params):
    result = cli.run(
        schema_url,
        *params,
        *multiple_params,
        *flags,
        *csv_params,
    )
    check_result(result)


@settings(
    deadline=None,
    phases=[Phase.explicit, Phase.generate],
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(schema=urls() | paths() | st.text(), base_url=urls() | paths() | st.text() | st.none())
@example(schema="//bla", base_url=None)
@example(schema="/\x00", base_url=None)
@example(schema="http://127.0.0.1/schema.yaml", base_url="//ÿ[")
@pytest.mark.usefixtures("mocked_schema")
def test_schema_validity(cli, schema, base_url):
    args = ()
    if base_url:
        args = (f"--url={base_url}",)
    result = cli.run(schema, *args)
    check_result(result)


def check_result(result):
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert DEFAULT_INTERNAL_ERROR_MESSAGE not in result.stdout, result.stdout


def remove_nones(value):
    if isinstance(value, dict):
        return {k: remove_nones(v) for k, v in value.items() if v is not None}
    elif isinstance(value, list):
        return [remove_nones(v) for v in value if v is not None]
    return value


@given(config=from_schema(deepclone(CONFIG_SCHEMA)).map(remove_nones))
@settings(
    phases=[Phase.generate],
    suppress_health_check=list(HealthCheck),
    deadline=None,
    database=None,
    verbosity=Verbosity.quiet,
    max_examples=7,
)
@pytest.mark.usefixtures("mocked_schema", "mocked_call")
def test_random_config(cli, config, schema_url, tmp_path):
    reports = config.get("reports", {})
    report_enabled = False
    for report_type in ("vcr", "har", "junit"):
        report = reports.get(report_type, {})
        if "path" in report:
            report["path"] = str(tmp_path / report["path"])
            report_enabled = True
        if report.get("enabled"):
            report_enabled = True
    # Add prefix to the 'directory' path if it exists
    if "directory" in reports:
        reports["directory"] = str(tmp_path / reports["directory"])
    elif report_enabled:
        reports["directory"] = str(tmp_path / "report")
    result = cli.main("run", schema_url, "-n 1", "--phases=examples", config=config)
    check_result(result)
