from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any

import hypothesis
from hypothesis import Phase, Verbosity
from hypothesis import strategies as st
from hypothesis._settings import all_settings
from hypothesis.errors import Unsatisfiable
from jsonschema_rs import ValidationError
from requests.models import CaseInsensitiveDict

from schemathesis.auths import AuthStorage, AuthStorageMark
from schemathesis.config import GenerationConfig, ProjectConfig
from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, NOT_SET, SpecificationFeature
from schemathesis.core.errors import (
    IncorrectUsage,
    InfiniteRecursiveReference,
    InvalidSchema,
    SerializationNotPossible,
    UnresolvableReference,
    is_regex_validation_error,
)
from schemathesis.core.marks import Mark
from schemathesis.core.parameters import LOCATION_TO_CONTAINER
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case, adjust_urlencoded_payload, find_invalid_headers
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis import examples, setup
from schemathesis.generation.hypothesis.examples import add_single_example
from schemathesis.generation.hypothesis.given import GivenInput, format_given_and_schema_examples_error
from schemathesis.hooks import (
    GLOBAL_HOOK_DISPATCHER,
    HookContext,
    HookDispatcher,
    HookDispatcherMark,
    dispatch_before_add_examples,
)
from schemathesis.schemas import APIOperation

setup()


class HypothesisTestMode(str, Enum):
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"


@dataclass(slots=True)
class HypothesisTestConfig:
    project: ProjectConfig
    modes: list[HypothesisTestMode]
    settings: hypothesis.settings | None = None
    explicit_settings: hypothesis.settings | None = None
    seed: int | None = None
    as_strategy_kwargs: dict[str, Any] = field(default_factory=dict)
    feedback: FeedbackSources = field(default_factory=FeedbackSources)
    given_args: tuple[GivenInput, ...] = ()
    given_kwargs: dict[str, GivenInput] = field(default_factory=dict)


def create_test(
    *,
    operation: APIOperation,
    test_func: Callable,
    config: HypothesisTestConfig,
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = HookDispatcherMark.get(test_func)
    auth_storage = AuthStorageMark.get(test_func)

    strategy_kwargs = {
        "hooks": hook_dispatcher,
        "auth_storage": auth_storage,
        "extra_data_source": config.feedback.extra_data_source,
        "error_feedback": config.feedback.error_feedback,
        "constants_value_source": config.feedback.constants_value_source,
        **config.as_strategy_kwargs,
    }
    generation = config.project.generation_for(operation=operation)
    strategy = st.one_of(operation.as_strategy(generation_mode=mode, **strategy_kwargs) for mode in generation.modes)

    hypothesis_test = create_base_test(
        test_function=test_func,
        strategy=strategy,
        args=config.given_args,
        kwargs=config.given_kwargs,
    )

    ApiOperationMark.set(hypothesis_test, operation)

    if config.seed is not None and not hasattr(test_func, "_hypothesis_internal_use_seed"):
        hypothesis_test = hypothesis.seed(config.seed)(hypothesis_test)

    # Get user's explicit settings from their @settings decorator (if present).
    # config.explicit_settings carries settings applied outside the lazy schema wrapper
    # (e.g. @settings applied after @lazy_schema.parametrize()).
    user_explicit_settings = getattr(test_func, SETTINGS_ATTRIBUTE_NAME, None) or config.explicit_settings

    # Get settings from the @given wrapper (inherits from loaded profile or default)
    given_settings = getattr(hypothesis_test, SETTINGS_ATTRIBUTE_NAME, None)
    assert given_settings is not None

    # Determine the source of user's settings:
    # - User's @settings decorator takes priority
    # - Otherwise use @given settings (which inherit from loaded profile or default)
    if user_explicit_settings is not None:
        user_settings = user_explicit_settings
    else:
        user_settings = given_settings

    default = hypothesis.settings.default
    if user_settings.verbosity == default.verbosity:
        user_settings = hypothesis.settings(user_settings, verbosity=Verbosity.quiet)

    if config.settings is not None:
        # Get hypothesis' built-in defaults (not affected by loaded profiles)
        hypothesis_defaults = hypothesis.settings.get_profile("default")

        # Merge strategy:
        # - Use schemathesis' config.settings as base (provides operational defaults)
        # - Override with user's customizations (values that differ from hypothesis built-in defaults)
        # This respects both @settings decorators and loaded profiles while allowing
        # schemathesis to set its operational requirements (deadline, verbosity, etc.)
        overrides = {
            item: getattr(user_settings, item)
            for item in all_settings
            if getattr(user_settings, item) != getattr(hypothesis_defaults, item)
        }
        settings = hypothesis.settings(config.settings, **overrides)
    else:
        settings = user_settings

    if Phase.explain in settings.phases:
        phases = tuple(phase for phase in settings.phases if phase != Phase.explain)
        settings = hypothesis.settings(settings, phases=phases)

    # Remove `reuse` & `generate` phases to avoid yielding any test cases if we don't do fuzzing
    if HypothesisTestMode.FUZZING not in config.modes and (
        Phase.generate in settings.phases or Phase.reuse in settings.phases
    ):
        phases = tuple(phase for phase in settings.phases if phase not in (Phase.reuse, Phase.generate))
        settings = hypothesis.settings(settings, phases=phases)

    specification = operation.schema.specification

    # Add examples if explicit phase is enabled
    if (
        HypothesisTestMode.EXAMPLES in config.modes
        and Phase.explicit in settings.phases
        and specification.supports_feature(SpecificationFeature.EXAMPLES)
    ):
        phases_config = config.project.phases_for(operation=operation)
        # Check if user provided custom strategies via @schema.given()
        # AND the operation actually has examples
        # These are incompatible because examples only provide 'case' while custom strategies require additional parameters
        if config.given_kwargs:
            # Check if there are actually examples to add
            try:
                example_strategies = list(operation.get_strategies_from_examples(**strategy_kwargs))
            except Exception:
                # If we can't get examples (invalid schema, etc), let add_examples handle it
                example_strategies = []

            if example_strategies:
                # Get the parameter names from given_kwargs to show in error message
                param_names = ", ".join(sorted(config.given_kwargs.keys()))
                raise IncorrectUsage(format_given_and_schema_examples_error(param_names))

        hypothesis_test = add_examples(
            hypothesis_test,
            operation,
            fill_missing=phases_config.examples.fill_missing,
            hook_dispatcher=hook_dispatcher,
            **strategy_kwargs,
        )

    if (
        HypothesisTestMode.COVERAGE in config.modes
        and Phase.explicit in settings.phases
        and specification.supports_feature(SpecificationFeature.COVERAGE)
        and not config.given_args
        and not config.given_kwargs
    ):
        hypothesis_test = add_coverage(
            hypothesis_test,
            operation,
            generation.modes,
            auth_storage,
            config.as_strategy_kwargs,
            feedback=config.feedback,
            generation_config=generation,
        )

    injected_path_parameter_names = [
        parameter.name
        for parameter in operation.path_parameters
        if parameter.definition.get(INJECTED_PATH_PARAMETER_KEY)
    ]
    if injected_path_parameter_names:
        names = ", ".join(f"'{name}'" for name in injected_path_parameter_names)
        plural = "s" if len(injected_path_parameter_names) > 1 else ""
        verb = "are" if len(injected_path_parameter_names) > 1 else "is"
        error = InvalidSchema(f"Path parameter{plural} {names} {verb} not defined")
        MissingPathParameters.set(hypothesis_test, error)

    setattr(hypothesis_test, SETTINGS_ATTRIBUTE_NAME, settings)

    return hypothesis_test


SETTINGS_ATTRIBUTE_NAME = "_hypothesis_internal_use_settings"


def create_base_test(
    *,
    test_function: Callable,
    strategy: st.SearchStrategy,
    args: tuple[GivenInput, ...],
    kwargs: dict[str, GivenInput],
) -> Callable:
    """Create the basic Hypothesis test with the given strategy."""

    @wraps(test_function)
    def test_wrapper(*args: Any, **kwargs: Any) -> Any:
        __tracebackhide__ = True
        return test_function(*args, **kwargs)

    funcobj = hypothesis.given(*args, **{**kwargs, "case": strategy})(test_wrapper)

    if inspect.iscoroutinefunction(test_function):
        funcobj.hypothesis.inner_test = make_async_test(test_function)
    return funcobj


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def add_examples(
    test: Callable,
    operation: APIOperation,
    fill_missing: bool,
    hook_dispatcher: HookDispatcher | None = None,
    **kwargs: Any,
) -> Callable:
    for example in generate_example_cases(
        test=test, operation=operation, fill_missing=fill_missing, hook_dispatcher=hook_dispatcher, **kwargs
    ):
        test = hypothesis.example(case=example)(test)

    return test


def generate_example_cases(
    *,
    test: Callable,
    operation: APIOperation,
    fill_missing: bool,
    hook_dispatcher: HookDispatcher | None = None,
    **kwargs: Any,
) -> Generator[Case]:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    try:
        result: list[Case] = [
            examples.generate_one(strategy)
            for strategy in operation.get_strategies_from_examples(
                fill_missing_from_pool=fill_missing,
                **kwargs,
            )
        ]
    except (
        InvalidSchema,
        InfiniteRecursiveReference,
        Unsatisfiable,
        UnresolvableReference,
        SerializationNotPossible,
        ValidationError,
    ) as exc:
        result = []
        if isinstance(exc, Unsatisfiable):
            UnsatisfiableExampleMark.set(test, exc)
        if isinstance(exc, SerializationNotPossible):
            NonSerializableMark.set(test, exc)
        if is_regex_validation_error(exc):
            InvalidRegexMark.set(test, exc)
        if isinstance(exc, InfiniteRecursiveReference):
            InfiniteRecursiveReferenceMark.set(test, exc)
        if isinstance(exc, UnresolvableReference):
            UnresolvableReferenceMark.set(test, exc)

    if fill_missing and not result:
        strategy = operation.as_strategy()
        add_single_example(strategy, result)

    context = HookContext(operation=operation)  # context should be passed here instead
    dispatchers: tuple[HookDispatcher, ...] = (GLOBAL_HOOK_DISPATCHER, operation.schema.hooks)
    if hook_dispatcher:
        dispatchers = (*dispatchers, hook_dispatcher)
    dispatch_before_add_examples(*dispatchers, context=context, examples=result)
    original_test = test
    for example in result:
        if example.headers is not None:
            invalid_headers = dict(find_invalid_headers(example.headers))
            if invalid_headers:
                InvalidHeadersExampleMark.set(original_test, invalid_headers)
                continue
        adjust_urlencoded_payload(example)
        yield example


def add_coverage(
    test: Callable,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    auth_storage: AuthStorage | None,
    as_strategy_kwargs: dict[str, Any],
    feedback: FeedbackSources,
    generation_config: GenerationConfig,
) -> Callable:
    from schemathesis.generation.drivers import CoverageGenerator

    generator = CoverageGenerator(
        operation=operation,
        generation_modes=generation_modes,
        generation_config=generation_config,
        auth_storage=auth_storage,
        as_strategy_kwargs=as_strategy_kwargs,
        feedback=feedback,
    )
    for case in generator:
        test = hypothesis.example(case=case)(test)
    return test


def _case_to_kwargs(case: Case) -> dict:
    kwargs = {}
    for container_name in LOCATION_TO_CONTAINER.values():
        value = getattr(case, container_name)
        if isinstance(value, CaseInsensitiveDict) and value:
            kwargs[container_name] = dict(value)
        elif value and value is not NOT_SET:
            kwargs[container_name] = value
    return kwargs


UnsatisfiableExampleMark = Mark[Unsatisfiable](attr_name="unsatisfiable_example")
NonSerializableMark = Mark[SerializationNotPossible](attr_name="non_serializable")
InvalidRegexMark = Mark[ValidationError](attr_name="invalid_regex")
InvalidHeadersExampleMark = Mark[dict[str, str]](attr_name="invalid_example_header")
MissingPathParameters = Mark[InvalidSchema](attr_name="missing_path_parameters")
InfiniteRecursiveReferenceMark = Mark[InfiniteRecursiveReference](attr_name="infinite_recursive_reference")
UnresolvableReferenceMark = Mark[UnresolvableReference](attr_name="unresolvable_reference")
ApiOperationMark = Mark[APIOperation](attr_name="api_operation")
