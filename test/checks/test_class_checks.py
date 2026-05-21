import datetime

import pytest

import schemathesis
from schemathesis.checks import ChecksConfig, RunChecks, collect_after_run_failures
from schemathesis.config import ConfigError
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.failures import FailureGroup, ResponseTimeExceeded
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase

_EMPTY_CONFIG = ChecksConfig()


def _shared_fail(message):
    raise AssertionError(message)


def _after_response(self, ctx, response, case):
    return None


def _after_run(self, ctx):
    return None


def _register(name, methods):
    return schemathesis.check(type(name, (), methods))


@pytest.mark.parametrize(
    ("methods", "as_response", "as_run"),
    [
        ({"after_response": _after_response, "after_run": _after_run}, True, True),
        ({"after_response": _after_response}, True, False),
        ({"after_run": _after_run}, False, True),
    ],
    ids=["both", "response-only", "run-only"],
)
def test_classification(restore_checks, methods, as_response, as_run):
    _register("Check", methods)
    run_checks = RunChecks.from_registry(_EMPTY_CONFIG)
    assert [name for name, *_ in run_checks.for_responses()] == (["Check"] if as_response else [])
    assert [name for name, _ in run_checks.for_run()] == (["Check"] if as_run else [])


def test_after_run_failures_are_distinct_per_check(ctx, restore_checks):
    @schemathesis.check
    class CheckA:
        def after_run(self, ctx):
            _shared_fail("boom")

    @schemathesis.check
    class CheckB:
        def after_run(self, ctx):
            _shared_fail("boom")

    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})
    run_checks = RunChecks.from_registry(schema.config.checks_config_for())
    failures = collect_after_run_failures(schema.config, run_checks.for_run())

    assert len(failures) == 2
    assert len(set(failures)) == 2


def test_reregistering_check_from_same_module_is_allowed(restore_checks):
    schemathesis.check(type("Dup", (), {"__module__": "pkg_a", "after_run": lambda self, ctx: None}))
    schemathesis.check(type("Dup", (), {"__module__": "pkg_a", "after_run": lambda self, ctx: None}))
    assert "Dup" in RunChecks.from_registry(_EMPTY_CONFIG).instances


def test_check_name_collision_with_builtin_raises(restore_checks):
    with pytest.raises(IncorrectUsage, match="already registered"):
        schemathesis.check(type("not_a_server_error", (), {"after_response": lambda self, c, r, ca: None}))


def test_get_by_name_does_not_mutate_unknown():
    config = ChecksConfig()
    config.get_by_name(name="SomeClassCheck")
    assert "SomeClassCheck" not in config._unknown


def test_unknown_check_name_in_config_raises(restore_checks):
    config = ChecksConfig.from_dict({"DefinitelyNotARegisteredCheck": {"threshold": 0.5}})
    with pytest.raises(ConfigError, match="DefinitelyNotARegisteredCheck"):
        RunChecks.from_registry(config)


def test_function_check_is_not_instantiated(restore_checks):
    @schemathesis.check
    def plain(ctx, response, case):
        return None

    assert "plain" not in RunChecks.from_registry(_EMPTY_CONFIG).instances


@pytest.mark.parametrize(
    ("methods", "match"),
    [
        ({"after_response_extra": _after_response}, "after_response_extra"),
        ({"helper": lambda self: None}, "at least one"),
        ({"after_response": lambda self, ctx: None}, "after_response"),
    ],
    ids=["unknown-method", "no-method", "wrong-arity"],
)
def test_invalid_class_is_rejected(restore_checks, methods, match):
    with pytest.raises(IncorrectUsage, match=match):
        _register("Invalid", methods)


def test_check_receives_config_kwargs(restore_checks):
    received = {}

    @schemathesis.check
    class Configurable:
        def __init__(self, *, threshold: float = 0.1):
            received["threshold"] = threshold

        def after_run(self, ctx):
            pass

    config = ChecksConfig.from_dict({"Configurable": {"threshold": 0.9}})
    RunChecks.from_registry(config=config)
    assert received["threshold"] == 0.9


def test_check_uses_default_when_no_config_kwargs(restore_checks):
    received = {}

    @schemathesis.check
    class Configurable:
        def __init__(self, *, threshold: float = 0.1):
            received["threshold"] = threshold

        def after_run(self, ctx):
            pass

    RunChecks.from_registry(_EMPTY_CONFIG)
    assert received["threshold"] == 0.1


def test_disabled_check_is_not_instantiated(restore_checks):
    @schemathesis.check
    class Disabled:
        def __init__(self):
            raise AssertionError("should not instantiate")

        def after_run(self, ctx):
            pass

    config = ChecksConfig()
    config.update(excluded_check_names=["Disabled"])
    run_checks = RunChecks.from_registry(config=config)

    assert "Disabled" not in run_checks.instances


def test_check_init_unknown_kwargs_raise_error(restore_checks):
    @schemathesis.check
    class NoKwargs:
        def __init__(self):
            pass

        def after_run(self, ctx):
            pass

    config = ChecksConfig.from_dict({"NoKwargs": {"irrelevant": 42}})
    with pytest.raises(ConfigError, match="irrelevant"):
        RunChecks.from_registry(config=config)


def test_check_with_var_kwargs_accepts_any_config_kwargs(restore_checks):
    received = {}

    @schemathesis.check
    class VarKwargs:
        def __init__(self, **kwargs):
            received.update(kwargs)

        def after_run(self, ctx):
            pass

    config = ChecksConfig.from_dict({"VarKwargs": {"threshold": 0.5, "extra": "yes"}})
    RunChecks.from_registry(config=config)
    assert received == {"threshold": 0.5, "extra": "yes"}


def test_check_init_unknown_key_with_one_known_shows_singular(restore_checks):
    @schemathesis.check
    class OneKnown:
        def __init__(self, *, threshold: float = 0.1):
            pass

        def after_run(self, ctx):
            pass

    config = ChecksConfig.from_dict({"OneKnown": {"threshold": 0.5, "unknown_key": 1}})
    with pytest.raises(ConfigError, match="Valid key: threshold"):
        RunChecks.from_registry(config=config)


def test_check_init_unknown_key_with_multiple_known_shows_plural(restore_checks):
    @schemathesis.check
    class TwoKnown:
        def __init__(self, *, min_count: int = 1, threshold: float = 0.1):
            pass

        def after_run(self, ctx):
            pass

    config = ChecksConfig.from_dict({"TwoKnown": {"unknown_key": 1}})
    with pytest.raises(ConfigError, match="Valid keys: min_count, threshold"):
        RunChecks.from_registry(config=config)


def test_custom_kwargs_populated_from_checks_config_from_dict(restore_checks):
    config = ChecksConfig.from_dict({"MyCheck": {"threshold": 0.9, "enabled": True}})
    assert config.custom_kwargs == {"MyCheck": {"threshold": 0.9}}


def test_check_init_exception_wrapped_in_config_error(restore_checks):
    @schemathesis.check
    class Broken:
        def __init__(self):
            raise ValueError("boom")

        def after_run(self, ctx):
            pass

    with pytest.raises(ConfigError, match="Failed to initialize check 'Broken': boom"):
        RunChecks.from_registry(_EMPTY_CONFIG)


def test_standalone_validation_passes_case_phase_to_class_check(ctx, response_factory, restore_checks):
    seen_phases = []

    @schemathesis.check
    class RecordPhase:
        def after_response(self, ctx, response, case):
            seen_phases.append(ctx.phase)

    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})
    case = schema["/test"]["GET"].Case(
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.1, mode=GenerationMode.POSITIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.FUZZING,
                data=FuzzingPhaseData(
                    description="",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
            ),
        )
    )

    case.validate_response(response_factory.requests(status_code=200))

    assert seen_phases == [PhaseName.FUZZING]


def test_class_based_check_fires_exactly_once_in_validate_response(ctx, response_factory, restore_checks):
    call_count = []

    @schemathesis.check
    class Counter:
        def after_response(self, ctx, response, case):
            call_count.append(1)

    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})
    case = schema["/test"]["GET"].Case()

    case.validate_response(response_factory.requests(status_code=200))

    assert len(call_count) == 1


def test_max_response_time_runs_in_validate_response(ctx, response_factory):
    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.checks.update(max_response_time=0.001)
    case = schema["/test"]["GET"].Case()
    response = response_factory.requests(status_code=200)
    response.elapsed = datetime.timedelta(seconds=2)

    with pytest.raises(FailureGroup) as exc:
        case.validate_response(response)

    assert any(isinstance(failure, ResponseTimeExceeded) for failure in exc.value.exceptions)


def test_disabled_class_based_check_skipped_in_validate_response(ctx, response_factory, restore_checks):
    called = []

    @schemathesis.check
    class ShouldSkip:
        def after_response(self, ctx, response, case):
            called.append(1)

    schema = ctx.openapi.load_schema({"/test": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.checks.update(excluded_check_names=["ShouldSkip"])
    case = schema["/test"]["GET"].Case()

    case.validate_response(response_factory.requests(status_code=200))

    assert called == []
