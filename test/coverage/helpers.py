from __future__ import annotations

from urllib.parse import parse_qs, unquote

import jsonschema_rs
import pytest
from hypothesis import Phase, settings
from requests.models import RequestEncodingMixin

import schemathesis
from schemathesis.core import NOT_SET
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.drivers import CoverageGenerator
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode, create_test
from schemathesis.generation.meta import CoverageScenario, TestPhase
from schemathesis.specs.openapi.coverage._operation import iter_coverage_cases
from schemathesis.specs.openapi.coverage._schema import cover_schema_iter
from test.utils import assert_requests_call

ALL_MODES = list(GenerationMode)
DEFAULT_RESPONSES = {"default": {"description": "OK"}}


def make_request_body(schema, *, media_type="application/json", required=True):
    body = {"content": {media_type: {"schema": schema}}}
    if required is not None:
        body["required"] = required
    return body


def build_schema(
    ctx,
    parameters=None,
    request_body=None,
    responses=None,
    version="3.0.2",
    path="/foo",
    method="post",
    *,
    body=None,
    media_type="application/json",
    body_required=True,
    **kwargs,
):
    operation = {"responses": responses if responses is not None else DEFAULT_RESPONSES}
    if body is not None and version.startswith("2"):
        parameters = [*(parameters or []), {"in": "body", "name": "body", "required": body_required, "schema": body}]
    elif body is not None:
        request_body = make_request_body(body, media_type=media_type, required=body_required)
    if parameters is not None:
        operation["parameters"] = parameters
    if request_body is not None:
        operation["requestBody"] = request_body
    return ctx.openapi.build_schema({path: {method: operation}}, version=version, **kwargs)


def load_schema(ctx, *args, **kwargs):
    return schemathesis.openapi.from_dict(build_schema(ctx, *args, **kwargs))


def body_operation(ctx, body, *, path="/foo", method="post", **kwargs):
    return load_schema(ctx, body=body, path=path, method=method, **kwargs)[path][method]


def run_test(operation, test, modes=ALL_MODES, generate_duplicate_query_parameters=None, unexpected_methods=None):
    # Mutate the operation's schema config directly: the coverage phase reads its settings off it.
    config = operation.schema.config
    config.generation.update(modes=modes)
    if generate_duplicate_query_parameters is not None:
        config.phases.coverage.generate_duplicate_query_parameters = generate_duplicate_query_parameters
    if unexpected_methods is not None:
        config.phases.coverage.unexpected_methods = unexpected_methods
    config.phases.examples.enabled = False
    config.phases.fuzzing.enabled = False
    config.phases.stateful.enabled = False
    test_func = create_test(
        operation=operation,
        test_func=test,
        config=HypothesisTestConfig(
            modes=[HypothesisTestMode.COVERAGE],
            project=config,
            settings=settings(phases=[Phase.explicit]),
        ),
    )
    test_func()


def run_positive_test(operation, test, **kwargs):
    return run_test(operation, test, [GenerationMode.POSITIVE], **kwargs)


def run_negative_test(operation, test, **kwargs):
    return run_test(operation, test, [GenerationMode.NEGATIVE], **kwargs)


def collect_cases(operation, mode, **kwargs):
    cases = []

    def collect(case):
        if case.meta.phase.name == TestPhase.COVERAGE:
            cases.append(case)

    run_test(operation, collect, [mode], **kwargs)
    return cases


def collect_coverage_cases(ctx, body_schema, positive=False, version="3.0.2"):
    # Positive bodies must pass schema validation, negative body-targeting ones must fail it.
    operation = body_operation(ctx, body_schema, version=version)
    validator = operation.schema.adapter.jsonschema_validator_cls(body_schema, validate_formats=True)
    mode = GenerationMode.POSITIVE if positive else GenerationMode.NEGATIVE
    cases = collect_cases(operation, mode)
    for case in cases:
        is_valid = validator.is_valid(case.body)
        body_is_target = case.meta.phase.data.parameter_location == ParameterLocation.BODY
        if positive and not is_valid:
            errors = [error.message for error in validator.iter_errors(case.body)]
            pytest.fail(
                f"Positive case produced invalid body.\nBody: {case.body}\nSchema: {body_schema}\nErrors: {errors}"
            )
        if not positive and body_is_target and is_valid:
            pytest.fail(
                f"Negative case produced valid body (should be invalid).\nBody: {case.body}\nSchema: {body_schema}\n"
                f"Scenario: {case.meta.phase.data.scenario}"
            )
    return cases


def iter_cases(operation, *generation_modes, **kwargs):
    return list(
        iter_coverage_cases(
            operation=operation,
            generation_modes=list(generation_modes),
            generate_duplicate_query_parameters=False,
            unexpected_methods=set(),
            generation_config=operation.schema.config.generation,
            **kwargs,
        )
    )


def generate_cases(operation, generation_mode):
    coverage_config = operation.schema.config.phases.coverage
    coverage_config.generate_duplicate_query_parameters = False
    coverage_config.unexpected_methods = set()
    return list(
        CoverageGenerator(
            operation=operation,
            generation_modes=[generation_mode],
            auth_storage=None,
            as_strategy_kwargs={},
            feedback=FeedbackSources(),
            generation_config=operation.schema.config.generation,
        )
    )


def optimized_body_schema(operation, media_type="application/json"):
    return next(alt.optimized_schema for alt in operation.body if alt.media_type == media_type)


def body_validator(operation, media_type="application/json", *, validate_formats=True, validator_cls=None):
    validator_cls = validator_cls or jsonschema_rs.validator_for
    return validator_cls(optimized_body_schema(operation, media_type), validate_formats=validate_formats)


def body_mode(case):
    info = case.meta.components.get(ParameterLocation.BODY) if case.meta else None
    return info.mode if info else None


def assert_bodies(operation, mode, *, valid, cases=None, source=iter_cases, validate_formats=True, validator_cls=None):
    # JSON bodies whose component was generated in `mode` must (not) conform to the schema. Returns them.
    validator = body_validator(operation, validate_formats=validate_formats, validator_cls=validator_cls)
    if cases is None:
        cases = source(operation, mode)
    bodies = [
        case.body
        for case in cases
        if case.body is not NOT_SET and case.media_type == "application/json" and body_mode(case) == mode
    ]
    assert bodies, f"No {mode.name} bodies generated"
    mismatched = [body for body in bodies if validator.is_valid(body) != valid]
    assert not mismatched, f"{mode.name} bodies are schema-{'invalid' if valid else 'valid'}: {mismatched!r}"
    return bodies


def scenario_cases(cases, scenario):
    return [case for case in cases if case.meta.phase.data.scenario == scenario]


def scenario_values(ctx, schema, scenario):
    return [value.value for value in cover_schema_iter(ctx, schema) if value.scenario is scenario]


def assert_positive_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.POSITIVE], expected, path)


def assert_negative_coverage(schema, expected, path=None):
    return assert_coverage(schema, [GenerationMode.NEGATIVE], expected, path)


def assert_coverage(schema, modes, expected, path=None):
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.phases.coverage.generate_duplicate_query_parameters = True

    cases = []
    operation = schema[path[0]][path[1]] if path else schema["/foo"]["post"]

    def test(case):
        meta = case.meta
        if meta.phase.name != TestPhase.COVERAGE:
            return
        if meta.phase.data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
            return
        assert_requests_call(case)
        mode = meta.generation.mode
        if len(modes) == 1:
            assert mode == modes[0]
        else:
            if mode == GenerationMode.POSITIVE:
                # If the main mode is positive, then all components should have the positive mode
                for component, info in case.meta.components.items():
                    assert info.mode == mode, f"{component.value} should have {mode.value} mode"
            if mode == GenerationMode.NEGATIVE:
                # If the main mode is negative, then at least one component should be negative
                assert any(info.mode == mode for info in case.meta.components.values())
        if (
            mode == GenerationMode.NEGATIVE
            and meta.phase.data.parameter_location
            in [
                "query",
                "path",
                "header",
                "cookie",
            ]
            and not (
                meta.phase.data.scenario == CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES
                and meta.phase.data.parameter is None
            )
        ):
            _validate_negative_parameter_serialization(case)

        if meta.phase.data.scenario == CoverageScenario.MAXIMUM_LENGTH_STRING:
            value, parameter = get_value_and_parameter(case)
            assert len(value) == parameter.definition["schema"]["maxLength"]

        output = {}
        for container in LOCATION_TO_CONTAINER.values():
            value = getattr(case, container)
            if container != "body" and not value:
                continue
            if value is not None and value is not NOT_SET:
                output[container] = value
        cases.append(output)

    run_test(operation, test, modes=modes, generate_duplicate_query_parameters=True)

    if isinstance(expected, tuple):
        assert cases in expected
    else:
        assert cases == expected


def get_value_and_parameter(case):
    location = LOCATION_TO_CONTAINER[case.meta.phase.data.parameter_location]
    name = case.meta.phase.data.parameter
    container = getattr(case, location)
    parameter = getattr(case.operation, location).get(name)
    return container.get(name), parameter


def _validate_negative_parameter_serialization(case):
    # Non-string negatives (`null`, `false`, `123`) may become schema-valid strings once serialized.
    value, parameter = get_value_and_parameter(case)
    data = case.meta.phase.data
    if data.scenario == CoverageScenario.MISSING_PARAMETER and parameter.definition.get("required"):
        return
    if data.scenario == CoverageScenario.DUPLICATE_PARAMETER:
        # Duplicate parameter is negative not in the schema sense
        return
    serialized_items = _get_serialized_parameter_values(value, data.parameter, data.parameter_location)
    _validate_serialized_items_are_negative(serialized_items, parameter, case)


def _get_serialized_parameter_values(value, parameter_name, location):
    if location == "query":
        return _serialize_query_parameter(value, parameter_name)
    elif location == "path":
        return [unquote(str(value))]
    return [str(value)]


def _serialize_query_parameter(value, parameter_name):
    encoded = RequestEncodingMixin._encode_params({parameter_name: value})
    if encoded == f"{parameter_name}=":
        return [""]
    elif not encoded:
        return []
    return parse_qs(encoded).get(parameter_name, [])


def _validate_serialized_items_are_negative(serialized_items, parameter, case):
    if not serialized_items:
        # Sending nothing is only negative when the parameter is required
        if not parameter.definition.get("required", False):
            pytest.fail(
                f"Generated empty parameter '{parameter.name}' but parameter is not required. "
                f"This creates a false positive in negative testing."
            )
        return

    schema = parameter.optimized_schema
    validator = case.operation.schema.adapter.jsonschema_validator_cls(schema)

    for item in serialized_items:
        try:
            validator.validate(item)
            pytest.fail(
                f"FALSE POSITIVE: Generated negative value became valid after serialization.\n"
                f"Parameter: {parameter.name}\n"
                f"Serialized value: '{item}'\n"
                f"Schema: {schema}\n"
                f"Description: {case.meta.phase.data.description}\n"
                f"This value should be invalid but passes validation after HTTP serialization."
            )
        except jsonschema_rs.ValidationError:
            pass
