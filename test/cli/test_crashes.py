from unittest import mock

import pytest
from hypothesis import HealthCheck, Phase, Verbosity, example, given, settings
from hypothesis import strategies as st
from hypothesis.provisional import urls
from requests import Response

from schemathesis import DataGenerationMethod
from schemathesis.cli import ALL_CHECKS_NAMES, ALL_TARGETS_NAMES
from schemathesis.code_samples import CodeSampleStyle
from schemathesis.experimental import GLOBAL_EXPERIMENTS
from schemathesis.fixups import ALL_FIXUPS
from schemathesis.runner.events import DEFAULT_INTERNAL_ERROR_MESSAGE
from schemathesis.stateful import Stateful


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
    key = draw(st.text(min_size=1))
    value = draw(st.text(min_size=1))
    return f"{key}:{value}"


@st.composite
def paths(draw):
    path = draw(st.text()).lstrip("/")
    return "/" + path


def csv_strategy(enum):
    return st.lists(st.sampled_from([item.name for item in enum]), min_size=1).map(",".join)


# The following strategies generate CLI parameters, for example "--workers=5" or "--exitfirst"
@settings(
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@given(
    params=st.fixed_dictionaries(
        {},
        optional={
            "auth": delimited(),
            "auth-type": st.sampled_from(["basic", "digest", "BASIC", "DIGEST"]),
            "data-generation-method": st.sampled_from([item.name for item in DataGenerationMethod]),
            "target": st.sampled_from(ALL_TARGETS_NAMES + ("all",)),
            "code-sample-style": st.sampled_from([item.name for item in CodeSampleStyle]),
            "fixups": st.sampled_from(list(ALL_FIXUPS) + ["all"]),
            "stateful": st.sampled_from([item.name for item in Stateful]),
            "force-schema-version": st.sampled_from(["20", "30"]),
            "workers": st.integers(min_value=1, max_value=64),
            "request-timeout": st.integers(),
            "stateful-recursion-limit": st.integers(),
            "max-response-time": st.integers(),
            "validate-schema": st.booleans(),
            "generation-with-security-parameters": st.booleans(),
            "hypothesis-database": st.text(),
            "hypothesis-deadline": st.integers() | st.none(),
            "hypothesis-max-examples": st.integers(),
            "hypothesis-report-multiple-bugs": st.booleans(),
            "hypothesis-seed": st.integers(),
            "hypothesis-verbosity": st.sampled_from([item.name for item in Verbosity]),
            "experimental": st.sampled_from([experiment.name for experiment in GLOBAL_EXPERIMENTS.available]),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
    flags=st.fixed_dictionaries(
        {},
        optional={
            key: st.booleans()
            for key in (
                "show-trace",
                "exitfirst",
                "hypothesis-derandomize",
                "dry-run",
                "skip-deprecated-operations",
                "no-color",
            )
        },
    ).map(lambda flags: [f"--{flag}" for flag in flags]),
    multiple_params=st.fixed_dictionaries(
        {},
        optional={
            "checks": st.lists(st.sampled_from(ALL_CHECKS_NAMES + ("all",)), min_size=1),
            "header": st.lists(delimited(), min_size=1),
            "endpoint": st.lists(st.text(min_size=1)),
            "method": st.lists(st.text(min_size=1)),
            "tag": st.lists(st.text(min_size=1)),
            "operation-id": st.lists(st.text(min_size=1)),
        },
    ).map(lambda params: [f"--{key}={value}" for key, values in params.items() for value in values]),
    csv_params=st.fixed_dictionaries(
        {},
        optional={
            "hypothesis-suppress-health-check": csv_strategy(HealthCheck),
            "hypothesis-phases": csv_strategy(Phase),
        },
    ).map(lambda params: [f"--{key}={value}" for key, value in params.items()]),
)
@example(params=[], flags=[], multiple_params=["--header=0:0\r"], csv_params=[])
@example(params=["--hypothesis-deadline=0"], flags=[], multiple_params=[], csv_params=[])
@example(params=["--hypothesis-deadline=86399999999999993"], flags=[], multiple_params=[], csv_params=[])
@example(params=["--hypothesis-max-examples=0"], flags=[], multiple_params=[], csv_params=[])
@pytest.mark.usefixtures("mocked_schema")
def test_valid_parameters_combos(cli, schema_url, params, flags, multiple_params, csv_params, tmp_path):
    report = tmp_path / "temp.tar.gz"
    debug = tmp_path / "debug.log"
    result = cli.run(
        schema_url,
        *params,
        *multiple_params,
        *flags,
        *csv_params,
        f"--report={report}",
        "--show-trace",
        f"--debug-output-file={debug}",
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
        args = (f"--base-url={base_url}",)
    result = cli.run(schema, *args)
    check_result(result)


def check_result(result):
    assert not (result.exception and not isinstance(result.exception, SystemExit)), result.stdout
    assert DEFAULT_INTERNAL_ERROR_MESSAGE not in result.stdout, result.stdout


def test_not_handled_error(mocker, cli, schema_url):
    # When there is an unhandled error in handlers
    mocker.patch("schemathesis.cli.output.default.handle_finished", side_effect=ValueError("Fail"))
    result = cli.run(schema_url)
    # Then it is propagated as it
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "Fail"
