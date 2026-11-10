from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import Any

import hypothesis
from hypothesis import Phase, Verbosity
from hypothesis import strategies as st
from hypothesis._settings import CI, all_settings

from schemathesis.auths import AuthStorage, AuthStorageMark
from schemathesis.config import GenerationConfig, ProjectConfig
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import IncorrectUsage, InvalidSchema
from schemathesis.core.marks import Mark
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.drivers import Controller, ExamplesGenerator, missing_path_parameters_error
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis import setup
from schemathesis.generation.hypothesis.given import GivenInput, format_given_and_schema_examples_error
from schemathesis.generation.meta import content_type_probes_first
from schemathesis.generation.overrides import Override
from schemathesis.hooks import HookDispatcher, HookDispatcherMark
from schemathesis.schemas import APIOperation

setup()


class HypothesisTestMode(StrEnum):
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"


@dataclass(slots=True)
class HypothesisTestConfig:
    project: ProjectConfig
    modes: list[HypothesisTestMode]
    settings: hypothesis.settings | None = None
    explicit_settings: hypothesis.settings | None = None
    # Hypothesis settings the Schemathesis config sets explicitly, even to their default values.
    explicit_setting_names: set[str] = field(default_factory=set)
    # Whether the `ci` profile Hypothesis loads on its own in CI environments applies; the CLI skips it.
    apply_ci_profile: bool = True
    seed: int | None = None
    override: Override = field(default_factory=Override)
    feedback: FeedbackSources = field(default_factory=FeedbackSources)
    given_args: tuple[GivenInput, ...] = ()
    given_kwargs: dict[str, GivenInput] = field(default_factory=dict)


def _fuzzing_strategy(
    operation: APIOperation, config: HypothesisTestConfig, strategy_kwargs: dict[str, Any]
) -> st.SearchStrategy[Case]:
    """Draw a case in any of the configured generation modes."""
    generation = config.project.generation_for(operation=operation, phase=HypothesisTestMode.FUZZING.value)
    return st.one_of(
        [
            operation.as_strategy(generation_mode=mode, feedback=config.feedback, **strategy_kwargs)
            for mode in generation.modes
        ]
    )


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
        **config.override.as_strategy_kwargs(),
    }
    hypothesis_test = create_base_test(
        test_function=test_func,
        strategy=_fuzzing_strategy(operation, config, strategy_kwargs),
        args=config.given_args,
        kwargs=config.given_kwargs,
    )

    ApiOperationMark.set(hypothesis_test, operation)

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
    # Hypothesis always has a loaded profile.
    assert default is not None
    if user_settings.verbosity == default.verbosity:
        user_settings = hypothesis.settings(user_settings, verbosity=Verbosity.quiet)

    if config.settings is not None:
        # Get hypothesis' built-in defaults (not affected by loaded profiles)
        hypothesis_defaults = hypothesis.settings.get_profile("default")

        # Values are whatever each Hypothesis setting accepts.
        overrides: dict[str, Any] = {}
        if hypothesis.settings.default is not CI:
            # Values from `@settings` or a loaded profile that differ from Hypothesis defaults override the config,
            # except that a loaded profile never overrides options set via CLI, config file or `update()`.
            overrides = {
                item: getattr(user_settings, item)
                for item in all_settings
                if getattr(user_settings, item) != getattr(hypothesis_defaults, item)
                and (user_explicit_settings is not None or item not in config.explicit_setting_names)
            }
        elif config.apply_ci_profile:
            # Hypothesis loads its `ci` profile on its own when it detects a CI environment. Precedence:
            # - User's @settings decorator: values that differ from the `ci` profile
            # - Schemathesis config: options set via CLI, config file or `update()`, whatever the value,
            #   and, for configs built in Python, values that differ from Hypothesis defaults
            # - `ci` profile
            for item in all_settings:
                user_value = getattr(user_settings, item)
                profile_value = getattr(CI, item)
                default_value = getattr(hypothesis_defaults, item)
                if user_value != profile_value:
                    overrides[item] = user_value
                    continue
                configured = item in config.explicit_setting_names or getattr(config.settings, item) != default_value
                # Derandomizing drops the example database, so a configured one keeps randomness
                drops_database = item == "derandomize" and config.settings.database is not None
                if not configured and not drops_database and profile_value != default_value:
                    overrides[item] = profile_value
        # Otherwise it is a CLI test under the skipped `ci` profile: without a @settings decorator,
        # the @given settings carry nothing but that profile.
        settings = hypothesis.settings(config.settings, **overrides)
    else:
        settings = user_settings

    if Phase.explain in settings.phases:
        phases: tuple[Phase, ...] = tuple(phase for phase in settings.phases if phase != Phase.explain)
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
                example_strategies = list(
                    operation.get_strategies_from_examples(feedback=config.feedback, **strategy_kwargs)
                )
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
            override=config.override,
            feedback=config.feedback,
            auth_storage=auth_storage,
            hooks=hook_dispatcher,
        )

    if (
        HypothesisTestMode.COVERAGE in config.modes
        and Phase.explicit in settings.phases
        and specification.supports_feature(SpecificationFeature.COVERAGE)
        and not config.given_args
        and not config.given_kwargs
    ):
        coverage_generation = config.project.generation_for(
            operation=operation, phase=HypothesisTestMode.COVERAGE.value
        )
        hypothesis_test = add_coverage(
            hypothesis_test,
            operation,
            coverage_generation.modes,
            auth_storage,
            config.override,
            feedback=config.feedback,
            generation_config=coverage_generation,
            hooks=hook_dispatcher,
        )

    missing_path_parameters = missing_path_parameters_error(operation)
    if missing_path_parameters is not None:
        MissingPathParameters.set(hypothesis_test, missing_path_parameters)

    # A seed takes precedence over `derandomize`, so deterministic mode gets one only when the user provided it.
    if (
        config.seed is not None
        and (not settings.derandomize or config.project.has_explicit_seed)
        and not hasattr(test_func, "_hypothesis_internal_use_seed")
    ):
        hypothesis_test = hypothesis.seed(config.seed)(hypothesis_test)

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
        # `hypothesis.inner_test` is documented Hypothesis API that its annotations leave out.
        funcobj.hypothesis.inner_test = make_async_test(test_function)  # type: ignore[attr-defined]
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
    *,
    fill_missing: bool,
    override: Override,
    feedback: FeedbackSources,
    auth_storage: AuthStorage | None,
    hooks: HookDispatcher | None,
) -> Callable:
    generator = ExamplesGenerator(
        operation=operation,
        override=override,
        feedback=feedback,
        fill_missing=fill_missing,
        hooks=hooks,
        auth_storage=auth_storage,
    )
    ExamplesControllerMark.set(test, generator.controller)
    _defer_explicit_examples(test, lambda: _as_explicit_examples(generator))
    return test


def add_coverage(
    test: Callable,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    auth_storage: AuthStorage | None,
    override: Override,
    feedback: FeedbackSources,
    generation_config: GenerationConfig,
    hooks: HookDispatcher | None = None,
) -> Callable:
    from schemathesis.generation.drivers import CoverageGenerator

    generator = CoverageGenerator(
        operation=operation,
        generation_modes=generation_modes,
        generation_config=generation_config,
        auth_storage=auth_storage,
        override=override,
        feedback=feedback,
        hooks=hooks,
    )

    def generate() -> list:
        examples = _as_explicit_examples(content_type_probes_first(generator))
        unserializable = generator.controller.unserializable_media_types
        if unserializable:
            UnserializablePayloadMark.set(test, sorted(unserializable))
        return examples

    _defer_explicit_examples(test, generate)
    return test


def _as_explicit_examples(cases: Iterable[Case]) -> list:
    # Only carries the attached examples; Hypothesis never calls it.
    def collector() -> None:
        raise NotImplementedError

    # Hypothesis runs explicit examples in reverse order of attachment.
    for case in cases:
        hypothesis.example(case=case)(collector)
    return getattr(collector, "hypothesis_explicit_examples", [])


def _defer_explicit_examples(test: Callable, generate: Callable[[], list]) -> None:
    """Append explicit examples that are generated on first read.

    Collecting a test must stay cheap: pytest builds every test up front, including skipped ones. Generating
    during the run also makes a generation error fail only this test.
    """
    examples = getattr(test, "hypothesis_explicit_examples", ())
    if not isinstance(examples, _DeferredExamples):
        examples = _DeferredExamples(examples)
        test.hypothesis_explicit_examples = examples  # type: ignore[attr-defined]
    examples.pending.append(generate)


class _DeferredExamples:
    """Explicit examples whose tail is generated on first read.

    Hypothesis only reverses this sequence and checks whether it is empty.
    """

    __slots__ = ("_examples", "pending")

    def __init__(self, examples: Iterable) -> None:
        self._examples = list(examples)
        self.pending: list[Callable[[], list]] = []

    def _materialized(self) -> list:
        while self.pending:
            self._examples.extend(self.pending[0]())
            # Dropped only once generated, so running a failed test again raises the same error.
            del self.pending[0]
        return self._examples

    def __reversed__(self) -> Iterator:
        return reversed(self._materialized())

    def __len__(self) -> int:
        return len(self._materialized())


ExamplesControllerMark = Mark[Controller](attr_name="examples_controller")
UnserializablePayloadMark = Mark[list[str]](attr_name="unserializable_payload")
MissingPathParameters = Mark[InvalidSchema](attr_name="missing_path_parameters")
ApiOperationMark = Mark[APIOperation](attr_name="api_operation")
