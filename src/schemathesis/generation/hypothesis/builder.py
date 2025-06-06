from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from itertools import combinations
from time import perf_counter
from typing import Any, Callable, Generator, Mapping

import hypothesis
from hypothesis import Phase, Verbosity
from hypothesis import strategies as st
from hypothesis._settings import all_settings
from hypothesis.errors import Unsatisfiable
from jsonschema.exceptions import SchemaError
from requests.models import CaseInsensitiveDict

from schemathesis import auths
from schemathesis.auths import AuthStorage, AuthStorageMark
from schemathesis.config import ProjectConfig
from schemathesis.core import NOT_SET, NotSet, SpecificationFeature, media_types
from schemathesis.core.errors import InvalidSchema, SerializationNotPossible
from schemathesis.core.marks import Mark
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.core.validation import has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode, coverage
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE, examples, setup, strategies
from schemathesis.generation.hypothesis.examples import add_single_example
from schemathesis.generation.hypothesis.given import GivenInput
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ComponentKind,
    CoveragePhaseData,
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
    settings: hypothesis.settings | None = None
    seed: int | None = None
    as_strategy_kwargs: dict[str, Any] = field(default_factory=dict)
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
        **config.as_strategy_kwargs,
    }
    generation = config.project.generation_for(operation=operation)
    strategy = strategies.combine(
        [operation.as_strategy(generation_mode=mode, **strategy_kwargs) for mode in generation.modes]
    )

    hypothesis_test = create_base_test(
        test_function=test_func,
        strategy=strategy,
        args=config.given_args,
        kwargs=config.given_kwargs,
    )

    if config.seed is not None:
        hypothesis_test = hypothesis.seed(config.seed)(hypothesis_test)

    default = hypothesis.settings.default
    settings = getattr(hypothesis_test, SETTINGS_ATTRIBUTE_NAME, None)
    assert settings is not None

    if settings.deadline == default.deadline:
        settings = hypothesis.settings(settings, deadline=DEFAULT_DEADLINE)

    if settings.verbosity == default.verbosity:
        settings = hypothesis.settings(settings, verbosity=Verbosity.quiet)

    if config.settings is not None:
        # Merge the user-provided settings with the current ones
        settings = hypothesis.settings(
            config.settings,
            **{
                item: getattr(settings, item)
                for item in all_settings
                if getattr(settings, item) != getattr(default, item)
            },
        )

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
        )

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

    if asyncio.iscoroutinefunction(test_function):
        funcobj.hypothesis.inner_test = make_async_test(test_function)  # type: ignore
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
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

    try:
        result: list[Case] = [
            examples.generate_one(strategy) for strategy in operation.get_strategies_from_examples(**kwargs)
        ]
    except (
        InvalidSchema,
        HypothesisRefResolutionError,
        Unsatisfiable,
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
        test = hypothesis.example(case=example)(test)

    return test


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
    unexpected_methods: set[str] | None = None,
) -> Callable:
    from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER

    auth_context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    overrides = {
        container: as_strategy_kwargs[container]
        for container in LOCATION_TO_CONTAINER.values()
        if container in as_strategy_kwargs
    }
    for case in _iter_coverage_cases(
        operation, generation_modes, generate_duplicate_query_parameters, unexpected_methods
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

        test = hypothesis.example(case=case)(test)
    return test


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
        self._components: dict[ComponentKind, ComponentInfo] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers

    def __contains__(self, key: str) -> bool:
        return key in self._template

    def __getitem__(self, key: str) -> dict:
        return self._template[key]

    def get(self, key: str, default: Any = None) -> dict:
        return self._template.get(key, default)

    def add_parameter(self, location: str, name: str, value: coverage.GeneratedValue) -> None:
        from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER

        component_name = LOCATION_TO_CONTAINER[location]
        kind = ComponentKind(component_name)
        info = self._components.get(kind)
        if info is None:
            self._components[kind] = ComponentInfo(mode=value.generation_mode)
        elif value.generation_mode == GenerationMode.NEGATIVE:
            info.mode = GenerationMode.NEGATIVE

        container = self._template.setdefault(component_name, {})
        container[name] = value.value

    def set_body(self, body: coverage.GeneratedValue, media_type: str) -> None:
        self._template["body"] = body.value
        self._template["media_type"] = media_type
        self._components[ComponentKind.BODY] = ComponentInfo(mode=body.generation_mode)

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
        kwargs = self._template.copy()
        kwargs = self._serialize(kwargs)
        return TemplateValue(kwargs=kwargs, components=self._components.copy())

    def with_body(self, *, media_type: str, value: coverage.GeneratedValue) -> TemplateValue:
        kwargs = {**self._template, "media_type": media_type, "body": value.value}
        kwargs = self._serialize(kwargs)
        components = {**self._components, ComponentKind.BODY: ComponentInfo(mode=value.generation_mode)}
        return TemplateValue(kwargs=kwargs, components=components)

    def with_parameter(self, *, location: str, name: str, value: coverage.GeneratedValue) -> TemplateValue:
        from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER

        container_name = LOCATION_TO_CONTAINER[location]
        container = self._template[container_name]
        return self.with_container(
            container_name=container_name, value={**container, name: value.value}, generation_mode=value.generation_mode
        )

    def with_container(self, *, container_name: str, value: Any, generation_mode: GenerationMode) -> TemplateValue:
        kwargs = {**self._template, container_name: value}
        components = {**self._components, ComponentKind(container_name): ComponentInfo(mode=generation_mode)}
        kwargs = self._serialize(kwargs)
        return TemplateValue(kwargs=kwargs, components=components)


@dataclass
class TemplateValue:
    kwargs: dict[str, Any]
    components: dict[ComponentKind, ComponentInfo]
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
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str] | None = None,
) -> Generator[Case, None, None]:
    from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
    from schemathesis.specs.openapi.examples import find_in_responses, find_matching_in_responses
    from schemathesis.specs.openapi.serialization import get_serializers_for_operation

    generators: dict[tuple[str, str], Generator[coverage.GeneratedValue, None, None]] = {}
    serializers = get_serializers_for_operation(operation)
    template = Template(serializers)

    instant = Instant()
    responses = find_in_responses(operation)
    # NOTE: The HEAD method is excluded
    unexpected_methods = unexpected_methods or {"get", "put", "post", "delete", "options", "patch", "trace"}

    seen_negative = coverage.HashSet()
    seen_positive = coverage.HashSet()

    for parameter in operation.iter_parameters():
        location = parameter.location
        name = parameter.name
        schema = parameter.as_json_schema(operation, update_quantifiers=False)
        for value in find_matching_in_responses(responses, parameter.name):
            schema.setdefault("examples", []).append(value)
        gen = coverage.cover_schema_iter(
            coverage.CoverageContext(location=location, generation_modes=generation_modes), schema
        )
        value = next(gen, NOT_SET)
        if isinstance(value, NotSet):
            continue
        template.add_parameter(location, name, value)
        generators[(location, name)] = gen
    template_time = instant.elapsed
    if operation.body:
        for body in operation.body:
            instant = Instant()
            schema = body.as_json_schema(operation, update_quantifiers=False)
            # Definition could be a list for Open API 2.0
            definition = body.definition if isinstance(body.definition, dict) else {}
            examples = [example["value"] for example in definition.get("examples", {}).values() if "value" in example]
            if examples:
                schema.setdefault("examples", []).extend(examples)
            gen = coverage.cover_schema_iter(
                coverage.CoverageContext(location="body", generation_modes=generation_modes), schema
            )
            value = next(gen, NOT_SET)
            if isinstance(value, NotSet):
                continue
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
                        description=value.description,
                        location=value.location,
                        parameter=body.media_type,
                        parameter_location="body",
                    ),
                ),
            )
            iterator = iter(gen)
            while True:
                instant = Instant()
                try:
                    next_value = next(iterator)
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
                                description=next_value.description,
                                location=next_value.location,
                                parameter=body.media_type,
                                parameter_location="body",
                            ),
                        ),
                    )
                except StopIteration:
                    break
    elif GenerationMode.POSITIVE in generation_modes:
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
                phase=PhaseInfo.coverage(description="Default positive test case"),
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
            elif value.generation_mode == GenerationMode.POSITIVE and not seen_positive.insert(data.kwargs):
                # Was already generated before
                continue

            yield operation.Case(
                **data.kwargs,
                _meta=CaseMetadata(
                    generation=GenerationInfo(time=instant.elapsed, mode=value.generation_mode),
                    components=data.components,
                    phase=PhaseInfo.coverage(
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
                    phase=PhaseInfo.coverage(description=f"Unspecified HTTP method: {method.upper()}"),
                ),
            )
        # Generate duplicate query parameters
        if generate_duplicate_query_parameters and operation.query:
            container = template["query"]
            for parameter in operation.query:
                instant = Instant()
                # Could be absent if value schema can't be negated
                # I.e. contains just `default` value without any other keywords
                value = container.get(parameter.name, NOT_SET)
                if value is not NOT_SET:
                    data = template.with_container(
                        container_name="query",
                        value={**container, parameter.name: [value, value]},
                        generation_mode=GenerationMode.NEGATIVE,
                    )
                    yield operation.Case(
                        **data.kwargs,
                        _meta=CaseMetadata(
                            generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                description=f"Duplicate `{parameter.name}` query parameter",
                                parameter=parameter.name,
                                parameter_location="query",
                            ),
                        ),
                    )
        # Generate missing required parameters
        for parameter in operation.iter_parameters():
            if parameter.is_required and parameter.location != "path":
                instant = Instant()
                name = parameter.name
                location = parameter.location
                container_name = LOCATION_TO_CONTAINER[location]
                container = template[container_name]
                data = template.with_container(
                    container_name=container_name,
                    value={k: v for k, v in container.items() if k != name},
                    generation_mode=GenerationMode.NEGATIVE,
                )
                yield operation.Case(
                    **data.kwargs,
                    _meta=CaseMetadata(
                        generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                        components=data.components,
                        phase=PhaseInfo.coverage(
                            description=f"Missing `{name}` at {location}",
                            parameter=name,
                            parameter_location=location,
                        ),
                    ),
                )
    # Generate combinations for each location
    for location, parameter_set in [
        ("query", operation.query),
        ("header", operation.headers),
        ("cookie", operation.cookies),
    ]:
        if not parameter_set:
            continue

        container_name = LOCATION_TO_CONTAINER[location]
        base_container = template.get(container_name, {})

        # Get required and optional parameters
        required = {p.name for p in parameter_set if p.is_required}
        all_params = {p.name for p in parameter_set}
        optional = sorted(all_params - required)

        # Helper function to create and yield a case
        def make_case(
            container_values: dict,
            description: str,
            _location: str,
            _container_name: str,
            _parameter: str | None,
            _generation_mode: GenerationMode,
            _instant: Instant,
        ) -> Case:
            data = template.with_container(
                container_name=_container_name, value=container_values, generation_mode=_generation_mode
            )
            return operation.Case(
                **data.kwargs,
                _meta=CaseMetadata(
                    generation=GenerationInfo(
                        time=_instant.elapsed,
                        mode=_generation_mode,
                    ),
                    components=data.components,
                    phase=PhaseInfo.coverage(
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
                    parameter.name: parameter.as_json_schema(operation)
                    for parameter in _parameter_set
                    if parameter.name in combination
                },
                "required": list(_required),
                "additionalProperties": False,
            }

        def _yield_negative(
            subschema: dict[str, Any], _location: str, _container_name: str
        ) -> Generator[Case, None, None]:
            iterator = iter(
                coverage.cover_schema_iter(
                    coverage.CoverageContext(location=_location, generation_modes=[GenerationMode.NEGATIVE]),
                    subschema,
                )
            )
            while True:
                instant = Instant()
                try:
                    more = next(iterator)
                    yield make_case(
                        more.value,
                        more.description,
                        _location,
                        _container_name,
                        more.parameter,
                        GenerationMode.NEGATIVE,
                        instant,
                    )
                except StopIteration:
                    break

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            if GenerationMode.POSITIVE in generation_modes:
                yield make_case(
                    only_required,
                    "Only required properties",
                    location,
                    container_name,
                    None,
                    GenerationMode.POSITIVE,
                    Instant(),
                )
            if GenerationMode.NEGATIVE in generation_modes:
                subschema = _combination_schema(only_required, required, parameter_set)
                for case in _yield_negative(subschema, location, container_name):
                    kwargs = _case_to_kwargs(case)
                    if not seen_negative.insert(kwargs):
                        continue
                    assert case.meta is not None
                    assert isinstance(case.meta.phase.data, CoveragePhaseData)
                    # Already generated in one of the blocks above
                    if location != "path" and not case.meta.phase.data.description.startswith(
                        "Missing required property"
                    ):
                        yield case

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container and GenerationMode.POSITIVE in generation_modes:
                yield make_case(
                    combo,
                    f"All required properties and optional '{opt_param}'",
                    location,
                    container_name,
                    None,
                    GenerationMode.POSITIVE,
                    Instant(),
                )
                if GenerationMode.NEGATIVE in generation_modes:
                    subschema = _combination_schema(combo, required, parameter_set)
                    for case in _yield_negative(subschema, location, container_name):
                        assert case.meta is not None
                        assert isinstance(case.meta.phase.data, CoveragePhaseData)
                        # Already generated in one of the blocks above
                        if location != "path" and not case.meta.phase.data.description.startswith(
                            "Missing required property"
                        ):
                            yield case

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if len(optional) > 1 and GenerationMode.POSITIVE in generation_modes:
            for size in range(2, len(optional)):
                for combination in combinations(optional, size):
                    combo = {k: v for k, v in base_container.items() if k in required or k in combination}
                    if combo != base_container:
                        yield make_case(
                            combo,
                            f"All required and {size} optional properties",
                            location,
                            container_name,
                            None,
                            GenerationMode.POSITIVE,
                            Instant(),
                        )


def _case_to_kwargs(case: Case) -> dict:
    from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER

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
