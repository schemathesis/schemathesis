from __future__ import annotations

import asyncio
import inspect
import warnings
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from itertools import combinations
from time import perf_counter
from typing import Any

import hypothesis
from hypothesis import Phase, Verbosity
from hypothesis import strategies as st
from hypothesis._settings import all_settings
from hypothesis.errors import Unsatisfiable
from jsonschema.exceptions import SchemaError
from requests.models import CaseInsensitiveDict

from schemathesis import auths
from schemathesis.auths import AuthStorage, AuthStorageMark
from schemathesis.config import GenerationConfig, ProjectConfig
from schemathesis.core import INJECTED_PATH_PARAMETER_KEY, NOT_SET, NotSet, SpecificationFeature, media_types
from schemathesis.core.errors import (
    IncorrectUsage,
    InfiniteRecursiveReference,
    InvalidSchema,
    MalformedMediaType,
    SerializationNotPossible,
    UnresolvableReference,
)
from schemathesis.core.marks import Mark
from schemathesis.core.parameters import LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.core.validation import has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode, coverage
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import examples, setup
from schemathesis.generation.hypothesis.examples import add_single_example
from schemathesis.generation.hypothesis.given import GivenInput, format_given_and_schema_examples_error
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoveragePhaseData,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookDispatcherMark
from schemathesis.schemas import APIOperation, ParameterSet

setup()


class HypothesisTestMode(str, Enum):
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"


@dataclass
class HypothesisTestConfig:
    project: ProjectConfig
    modes: list[HypothesisTestMode]
    settings: hypothesis.settings | None
    seed: int | None
    as_strategy_kwargs: dict[str, Any]
    given_args: tuple[GivenInput, ...]
    given_kwargs: dict[str, GivenInput]

    __slots__ = (
        "project",
        "modes",
        "settings",
        "seed",
        "as_strategy_kwargs",
        "given_args",
        "given_kwargs",
    )

    def __init__(
        self,
        project: ProjectConfig,
        modes: list[HypothesisTestMode],
        settings: hypothesis.settings | None = None,
        seed: int | None = None,
        as_strategy_kwargs: dict[str, Any] | None = None,
        given_args: tuple[GivenInput, ...] = (),
        given_kwargs: dict[str, GivenInput] | None = None,
    ) -> None:
        self.project = project
        self.modes = modes
        self.settings = settings
        self.seed = seed
        self.as_strategy_kwargs = as_strategy_kwargs or {}
        self.given_args = given_args
        self.given_kwargs = given_kwargs or {}


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

    # Get user's explicit settings from their @settings decorator (if present)
    user_explicit_settings = getattr(test_func, SETTINGS_ATTRIBUTE_NAME, None)

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
        phases_config = config.project.phases_for(operation=operation)
        hypothesis_test = add_coverage(
            hypothesis_test,
            operation,
            generation.modes,
            auth_storage,
            config.as_strategy_kwargs,
            generate_duplicate_query_parameters=phases_config.coverage.generate_duplicate_query_parameters,
            unexpected_methods=phases_config.coverage.unexpected_methods,
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
            examples.generate_one(strategy) for strategy in operation.get_strategies_from_examples(**kwargs)
        ]
    except (
        InvalidSchema,
        InfiniteRecursiveReference,
        Unsatisfiable,
        UnresolvableReference,
        SerializationNotPossible,
        SchemaError,
    ) as exc:
        result = []
        if isinstance(exc, Unsatisfiable):
            UnsatisfiableExampleMark.set(test, exc)
        if isinstance(exc, SerializationNotPossible):
            NonSerializableMark.set(test, exc)
        if isinstance(exc, SchemaError):
            InvalidRegexMark.set(test, exc)
        if isinstance(exc, InfiniteRecursiveReference):
            InfiniteRecursiveReferenceMark.set(test, exc)
        if isinstance(exc, UnresolvableReference):
            UnresolvableReferenceMark.set(test, exc)

    if fill_missing and not result:
        strategy = operation.as_strategy()
        add_single_example(strategy, result)

    context = HookContext(operation=operation)  # context should be passed here instead
    GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, result)
    operation.schema.hooks.dispatch("before_add_examples", context, result)
    if hook_dispatcher:
        hook_dispatcher.dispatch("before_add_examples", context, result)
    original_test = test
    for example in result:
        if example.headers is not None:
            invalid_headers = dict(find_invalid_headers(example.headers))
            if invalid_headers:
                InvalidHeadersExampleMark.set(original_test, invalid_headers)
                continue
        adjust_urlencoded_payload(example)
        yield example


def adjust_urlencoded_payload(case: Case) -> None:
    if case.media_type is not None:
        try:
            media_type = media_types.parse(case.media_type)
            if media_type == ("application", "x-www-form-urlencoded"):
                case.body = prepare_urlencoded(case.body)
        except ValueError:
            pass


def add_coverage(
    test: Callable,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    auth_storage: AuthStorage | None,
    as_strategy_kwargs: dict[str, Any],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str],
    generation_config: GenerationConfig,
) -> Callable:
    for case in generate_coverage_cases(
        operation=operation,
        generation_modes=generation_modes,
        auth_storage=auth_storage,
        as_strategy_kwargs=as_strategy_kwargs,
        generate_duplicate_query_parameters=generate_duplicate_query_parameters,
        unexpected_methods=unexpected_methods,
        generation_config=generation_config,
    ):
        test = hypothesis.example(case=case)(test)
    return test


def generate_coverage_cases(
    *,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    auth_storage: AuthStorage | None,
    as_strategy_kwargs: dict[str, Any],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str],
    generation_config: GenerationConfig,
) -> Generator[Case]:
    from schemathesis.core.parameters import LOCATION_TO_CONTAINER

    auth_context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    overrides = {
        container: as_strategy_kwargs[container]
        for container in LOCATION_TO_CONTAINER.values()
        if container in as_strategy_kwargs
    }
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*but this is not valid syntax for a Python regular expression.*", category=UserWarning
        )
        for case in _iter_coverage_cases(
            operation=operation,
            generation_modes=generation_modes,
            generate_duplicate_query_parameters=generate_duplicate_query_parameters,
            unexpected_methods=unexpected_methods,
            generation_config=generation_config,
        ):
            if case.media_type and operation.schema.transport.get_first_matching_media_type(case.media_type) is None:
                continue
            adjust_urlencoded_payload(case)
            auths.set_on_case(case, auth_context, auth_storage)
            for container_name, value in overrides.items():
                container = getattr(case, container_name)
                if container is None:
                    setattr(case, container_name, value)
                else:
                    container.update(value)
            yield case


class Instant:
    __slots__ = ("start",)

    def __init__(self) -> None:
        self.start = perf_counter()

    @property
    def elapsed(self) -> float:
        return perf_counter() - self.start


class Template:
    __slots__ = ("_components", "_template", "_serializers")

    def __init__(self, serializers: dict[str, Callable]) -> None:
        self._components: dict[ParameterLocation, ComponentInfo] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers

    def __contains__(self, key: str) -> bool:
        return key in self._template

    def __getitem__(self, key: str) -> dict:
        return self._template[key]

    def get(self, key: str, default: Any = None) -> dict:
        return self._template.get(key, default)

    def add_parameter(self, location: ParameterLocation, name: str, value: coverage.GeneratedValue) -> None:
        info = self._components.get(location)
        if info is None:
            self._components[location] = ComponentInfo(mode=value.generation_mode)
        elif value.generation_mode == GenerationMode.NEGATIVE:
            info.mode = GenerationMode.NEGATIVE

        container = self._template.setdefault(location.container_name, {})
        container[name] = value.value

    def set_body(self, body: coverage.GeneratedValue, media_type: str) -> None:
        self._template["body"] = body.value
        self._template["media_type"] = media_type
        self._components[ParameterLocation.BODY] = ComponentInfo(mode=body.generation_mode)

    def _serialize(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from schemathesis.specs.openapi._hypothesis import quote_all

        output = {}
        for container_name, value in kwargs.items():
            serializer = self._serializers.get(container_name)
            if container_name in ("headers", "cookies") and isinstance(value, dict):
                value = _stringify_value(value, container_name)
            if serializer is not None:
                value = serializer(value)
            if container_name == "query" and isinstance(value, dict):
                value = _stringify_value(value, container_name)
            if container_name == "path_parameters" and isinstance(value, dict):
                value = _stringify_value(quote_all(value), container_name)
            output[container_name] = value
        return output

    def unmodified(self) -> TemplateValue:
        kwargs = deepclone(self._template)
        kwargs = self._serialize(kwargs)
        return TemplateValue(kwargs=kwargs, components=self._components.copy())

    def with_body(self, *, media_type: str, value: coverage.GeneratedValue) -> TemplateValue:
        kwargs = {**self._template, "media_type": media_type, "body": value.value}
        kwargs = self._serialize(kwargs)
        components = {**self._components, ParameterLocation.BODY: ComponentInfo(mode=value.generation_mode)}
        return TemplateValue(kwargs=kwargs, components=components)

    def with_parameter(
        self, *, location: ParameterLocation, name: str, value: coverage.GeneratedValue
    ) -> TemplateValue:
        container = self._template[location.container_name]
        return self.with_location(
            location=location,
            value={**container, name: value.value},
            generation_mode=value.generation_mode,
        )

    def with_location(
        self, *, location: ParameterLocation, value: Any, generation_mode: GenerationMode
    ) -> TemplateValue:
        kwargs = {**self._template, location.container_name: value}
        components = {**self._components, location: ComponentInfo(mode=generation_mode)}
        kwargs = self._serialize(kwargs)
        return TemplateValue(kwargs=kwargs, components=components)


@dataclass
class TemplateValue:
    kwargs: dict[str, Any]
    components: dict[ParameterLocation, ComponentInfo]

    __slots__ = ("kwargs", "components")


def _stringify_value(val: Any, container_name: str) -> Any:
    if val is None:
        return "null"
    if val is True:
        return "true"
    if val is False:
        return "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if container_name == "query":
            # Having a list here ensures there will be multiple query parameters wit the same name
            return [_stringify_value(item, container_name) for item in val]
        # use comma-separated values style for arrays
        return ",".join(str(_stringify_value(sub, container_name)) for sub in val)
    if isinstance(val, dict):
        return {key: _stringify_value(sub, container_name) for key, sub in val.items()}
    return val


def _iter_coverage_cases(
    *,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str],
    generation_config: GenerationConfig,
) -> Generator[Case, None, None]:
    from schemathesis.specs.openapi._hypothesis import _build_custom_formats
    from schemathesis.specs.openapi.examples import find_matching_in_responses
    from schemathesis.specs.openapi.media_types import MEDIA_TYPES
    from schemathesis.specs.openapi.schemas import OpenApiSchema
    from schemathesis.specs.openapi.serialization import get_serializers_for_operation

    generators: dict[tuple[ParameterLocation, str], Generator[coverage.GeneratedValue, None, None]] = {}
    serializers = get_serializers_for_operation(operation)
    template = Template(serializers)

    instant = Instant()
    responses = list(operation.responses.iter_examples())
    custom_formats = _build_custom_formats(generation_config)

    seen_negative = coverage.HashSet()
    seen_positive = coverage.HashSet()
    assert isinstance(operation.schema, OpenApiSchema)
    validator_cls = operation.schema.adapter.jsonschema_validator_cls

    for parameter in operation.iter_parameters():
        location = parameter.location
        name = parameter.name
        schema = parameter.unoptimized_schema
        examples = parameter.examples
        if examples:
            schema = dict(schema)
            schema["examples"] = examples
        for value in find_matching_in_responses(responses, parameter.name):
            schema.setdefault("examples", []).append(value)
        gen = coverage.cover_schema_iter(
            coverage.CoverageContext(
                root_schema=schema,
                location=location,
                media_type=None,
                generation_modes=generation_modes,
                is_required=parameter.is_required,
                custom_formats=custom_formats,
                validator_cls=validator_cls,
                allow_extra_parameters=generation_config.allow_extra_parameters,
            ),
            schema,
        )
        value = next(gen, NOT_SET)
        if isinstance(value, NotSet):
            if location == ParameterLocation.PATH:
                # Can't skip path parameters - they should be filled
                schema = dict(schema)
                schema.setdefault("type", "string")
                schema.setdefault("minLength", 1)
                gen = coverage.cover_schema_iter(
                    coverage.CoverageContext(
                        root_schema=schema,
                        location=location,
                        media_type=None,
                        generation_modes=[GenerationMode.POSITIVE],
                        is_required=parameter.is_required,
                        custom_formats=custom_formats,
                        validator_cls=validator_cls,
                        allow_extra_parameters=generation_config.allow_extra_parameters,
                    ),
                    schema,
                )
                value = next(
                    gen,
                    coverage.GeneratedValue(
                        "value",
                        generation_mode=GenerationMode.NEGATIVE,
                        scenario=CoverageScenario.UNSUPPORTED_PATH_PATTERN,
                        description="Sample value for unsupported path parameter pattern",
                        parameter=name,
                        location="/",
                    ),
                )
                template.add_parameter(location, name, value)
                continue
            continue
        template.add_parameter(location, name, value)
        generators[(location, name)] = gen
    template_time = instant.elapsed
    has_required_body = operation.body and any(b.is_required for b in operation.body)
    has_generated_required_body = False
    if operation.body:
        for body in operation.body:
            instant = Instant()
            schema = body.unoptimized_schema
            examples = body.examples
            if examples:
                schema = dict(schema)
                # User-registered media types should only handle text / binary data
                if body.media_type in MEDIA_TYPES:
                    schema["examples"] = [example for example in examples if isinstance(example, (str, bytes))]
                else:
                    schema["examples"] = examples
            try:
                media_type = media_types.parse(body.media_type)
            except MalformedMediaType:
                media_type = None
            gen = coverage.cover_schema_iter(
                coverage.CoverageContext(
                    root_schema=schema,
                    location=ParameterLocation.BODY,
                    media_type=media_type,
                    generation_modes=generation_modes,
                    is_required=body.is_required,
                    custom_formats=custom_formats,
                    validator_cls=validator_cls,
                    allow_extra_parameters=generation_config.allow_extra_parameters,
                ),
                schema,
            )
            value = next(gen, NOT_SET)
            if isinstance(value, NotSet) or (
                body.media_type in MEDIA_TYPES and not isinstance(value.value, (str, bytes))
            ):
                continue
            if body.is_required:
                has_generated_required_body = True
            elapsed = instant.elapsed
            if "body" not in template:
                template_time += elapsed
                template.set_body(value, body.media_type)
            data = template.with_body(value=value, media_type=body.media_type)
            yield operation.Case(
                **data.kwargs,
                _meta=CaseMetadata(
                    generation=GenerationInfo(
                        time=elapsed,
                        mode=value.generation_mode,
                    ),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=value.scenario,
                        description=value.description,
                        location=value.location,
                        parameter=body.media_type,
                        parameter_location=ParameterLocation.BODY,
                    ),
                ),
            )
            iterator = iter(gen)
            while True:
                instant = Instant()
                try:
                    next_value = next(iterator)
                    if body.media_type in MEDIA_TYPES and not isinstance(next_value.value, (str, bytes)):
                        continue

                    data = template.with_body(value=next_value, media_type=body.media_type)
                    yield operation.Case(
                        **data.kwargs,
                        _meta=CaseMetadata(
                            generation=GenerationInfo(
                                time=instant.elapsed,
                                mode=next_value.generation_mode,
                            ),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=next_value.scenario,
                                description=next_value.description,
                                location=next_value.location,
                                parameter=body.media_type,
                                parameter_location=ParameterLocation.BODY,
                            ),
                        ),
                    )
                except StopIteration:
                    break
    elif GenerationMode.POSITIVE in generation_modes and (not has_required_body or has_generated_required_body):
        data = template.unmodified()
        seen_positive.insert(data.kwargs)
        yield operation.Case(
            **data.kwargs,
            _meta=CaseMetadata(
                generation=GenerationInfo(
                    time=template_time,
                    mode=GenerationMode.POSITIVE,
                ),
                components=data.components,
                phase=PhaseInfo.coverage(
                    scenario=CoverageScenario.DEFAULT_POSITIVE_TEST, description="Default positive test case"
                ),
            ),
        )

    for (location, name), gen in generators.items():
        iterator = iter(gen)
        while True:
            instant = Instant()
            try:
                value = next(iterator)
                data = template.with_parameter(location=location, name=name, value=value)
            except StopIteration:
                break

            if value.generation_mode == GenerationMode.NEGATIVE:
                seen_negative.insert(data.kwargs)
            elif value.generation_mode == GenerationMode.POSITIVE:
                if has_required_body and not has_generated_required_body:
                    continue
                if not seen_positive.insert(data.kwargs):
                    continue

            yield operation.Case(
                **data.kwargs,
                _meta=CaseMetadata(
                    generation=GenerationInfo(time=instant.elapsed, mode=value.generation_mode),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=value.scenario,
                        description=value.description,
                        location=value.location,
                        parameter=name,
                        parameter_location=location,
                    ),
                ),
            )
    if GenerationMode.NEGATIVE in generation_modes:
        # Generate HTTP methods that are not specified in the spec
        methods = unexpected_methods - set(operation.schema[operation.path])
        for method in sorted(methods):
            instant = Instant()
            data = template.unmodified()
            yield operation.Case(
                **data.kwargs,
                method=method.upper(),
                _meta=CaseMetadata(
                    generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                        description=f"Unspecified HTTP method: {method.upper()}",
                    ),
                ),
            )
        # Generate duplicate query parameters
        # NOTE: if the query schema has no constraints, then we may have no negative test cases at all
        # as they all will match the original schema and therefore will be considered as positive ones
        if generate_duplicate_query_parameters and operation.query and "query" in template:
            container = template["query"]
            for parameter in operation.query:
                instant = Instant()
                # Could be absent if value schema can't be negated
                # I.e. contains just `default` value without any other keywords
                value = container.get(parameter.name, NOT_SET)
                if value is not NOT_SET:
                    data = template.with_location(
                        location=ParameterLocation.QUERY,
                        value={**container, parameter.name: [value, value]},
                        generation_mode=GenerationMode.NEGATIVE,
                    )
                    yield operation.Case(
                        **data.kwargs,
                        _meta=CaseMetadata(
                            generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=CoverageScenario.DUPLICATE_PARAMETER,
                                description=f"Duplicate `{parameter.name}` query parameter",
                                parameter=parameter.name,
                                parameter_location=ParameterLocation.QUERY,
                            ),
                        ),
                    )
        # Generate missing required parameters
        for parameter in operation.iter_parameters():
            if parameter.is_required and parameter.location != ParameterLocation.PATH:
                instant = Instant()
                name = parameter.name
                location = parameter.location
                container = template.get(location.container_name, {})
                data = template.with_location(
                    location=location,
                    value={k: v for k, v in container.items() if k != name},
                    generation_mode=GenerationMode.NEGATIVE,
                )

                if seen_negative.insert(data.kwargs):
                    yield operation.Case(
                        **data.kwargs,
                        _meta=CaseMetadata(
                            generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=CoverageScenario.MISSING_PARAMETER,
                                description=f"Missing `{name}` at {location.value}",
                                parameter=name,
                                parameter_location=location,
                            ),
                        ),
                    )
    # Generate combinations for each location
    for location, parameter_set in [
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
    ]:
        if not parameter_set:
            continue

        container_name = location.container_name
        base_container = template.get(container_name, {})

        # Get required and optional parameters
        required = {p.name for p in parameter_set if p.is_required}
        all_params = {p.name for p in parameter_set}
        optional = sorted(all_params - required)

        # Helper function to create and yield a case
        def make_case(
            container_values: dict,
            scenario: CoverageScenario,
            description: str,
            _location: ParameterLocation,
            _parameter: str | None,
            _generation_mode: GenerationMode,
            _instant: Instant,
        ) -> Case:
            data = template.with_location(location=_location, value=container_values, generation_mode=_generation_mode)
            return operation.Case(
                **data.kwargs,
                _meta=CaseMetadata(
                    generation=GenerationInfo(
                        time=_instant.elapsed,
                        mode=_generation_mode,
                    ),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=scenario,
                        description=description,
                        parameter=_parameter,
                        parameter_location=_location,
                    ),
                ),
            )

        def _combination_schema(
            combination: dict[str, Any], _required: set[str], _parameter_set: ParameterSet
        ) -> dict[str, Any]:
            return {
                "properties": {
                    parameter.name: parameter.optimized_schema
                    for parameter in _parameter_set
                    if parameter.name in combination
                },
                "required": list(_required),
                "additionalProperties": False,
            }

        def _yield_negative(
            subschema: dict[str, Any], _location: ParameterLocation, is_required: bool
        ) -> Generator[Case, None, None]:
            iterator = iter(
                coverage.cover_schema_iter(
                    coverage.CoverageContext(
                        root_schema=subschema,
                        location=_location,
                        media_type=None,
                        generation_modes=[GenerationMode.NEGATIVE],
                        is_required=is_required,
                        custom_formats=custom_formats,
                        validator_cls=validator_cls,
                        allow_extra_parameters=generation_config.allow_extra_parameters,
                    ),
                    subschema,
                )
            )
            while True:
                instant = Instant()
                try:
                    more = next(iterator)
                    yield make_case(
                        more.value,
                        more.scenario,
                        more.description,
                        _location,
                        more.parameter,
                        GenerationMode.NEGATIVE,
                        instant,
                    )
                except StopIteration:
                    break

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            if GenerationMode.POSITIVE in generation_modes and not (
                has_required_body and not has_generated_required_body
            ):
                yield make_case(
                    only_required,
                    CoverageScenario.OBJECT_ONLY_REQUIRED,
                    "Only required properties",
                    location,
                    None,
                    GenerationMode.POSITIVE,
                    Instant(),
                )
            if GenerationMode.NEGATIVE in generation_modes:
                subschema = _combination_schema(only_required, required, parameter_set)
                for case in _yield_negative(subschema, location, is_required=bool(required)):
                    kwargs = _case_to_kwargs(case)
                    if not seen_negative.insert(kwargs):
                        continue
                    assert case.meta is not None
                    assert isinstance(case.meta.phase.data, CoveragePhaseData)
                    # Already generated in one of the blocks above
                    if (
                        location != "path"
                        and case.meta.phase.data.scenario != CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
                    ):
                        yield case

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container and GenerationMode.POSITIVE in generation_modes:
                if not (has_required_body and not has_generated_required_body):
                    yield make_case(
                        combo,
                        CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                        f"All required properties and optional '{opt_param}'",
                        location,
                        None,
                        GenerationMode.POSITIVE,
                        Instant(),
                    )
                if GenerationMode.NEGATIVE in generation_modes:
                    subschema = _combination_schema(combo, required, parameter_set)
                    for case in _yield_negative(subschema, location, is_required=bool(required)):
                        assert case.meta is not None
                        assert isinstance(case.meta.phase.data, CoveragePhaseData)
                        # Already generated in one of the blocks above
                        if (
                            location != "path"
                            and case.meta.phase.data.scenario != CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
                        ):
                            yield case

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if (
            len(optional) > 1
            and GenerationMode.POSITIVE in generation_modes
            and not (has_required_body and not has_generated_required_body)
        ):
            for size in range(2, len(optional)):
                for combination in combinations(optional, size):
                    combo = {k: v for k, v in base_container.items() if k in required or k in combination}
                    if combo != base_container:
                        yield make_case(
                            combo,
                            CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                            f"All required and {size} optional properties",
                            location,
                            None,
                            GenerationMode.POSITIVE,
                            Instant(),
                        )


def _case_to_kwargs(case: Case) -> dict:
    kwargs = {}
    for container_name in LOCATION_TO_CONTAINER.values():
        value = getattr(case, container_name)
        if isinstance(value, CaseInsensitiveDict) and value:
            kwargs[container_name] = dict(value)
        elif value and value is not NOT_SET:
            kwargs[container_name] = value
    return kwargs


def find_invalid_headers(headers: Mapping) -> Generator[tuple[str, str], None, None]:
    for name, value in headers.items():
        if not is_latin_1_encodable(value) or has_invalid_characters(name, value):
            yield name, value


UnsatisfiableExampleMark = Mark[Unsatisfiable](attr_name="unsatisfiable_example")
NonSerializableMark = Mark[SerializationNotPossible](attr_name="non_serializable")
InvalidRegexMark = Mark[SchemaError](attr_name="invalid_regex")
InvalidHeadersExampleMark = Mark[dict[str, str]](attr_name="invalid_example_header")
MissingPathParameters = Mark[InvalidSchema](attr_name="missing_path_parameters")
InfiniteRecursiveReferenceMark = Mark[InfiniteRecursiveReference](attr_name="infinite_recursive_reference")
UnresolvableReferenceMark = Mark[UnresolvableReference](attr_name="unresolvable_reference")
ApiOperationMark = Mark[APIOperation](attr_name="api_operation")
