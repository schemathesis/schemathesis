from __future__ import annotations

import asyncio
import json
import warnings
from functools import wraps
from itertools import combinations
from time import perf_counter
from typing import Any, Callable, Generator, Mapping

import hypothesis
from hypothesis import Phase
from hypothesis.errors import HypothesisWarning, Unsatisfiable
from jsonschema.exceptions import SchemaError

from schemathesis.auths import AuthStorageMark
from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.errors import InvalidSchema, SerializationNotPossible
from schemathesis.core.marks import Mark
from schemathesis.core.result import Ok, Result
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.core.validation import has_invalid_characters, is_latin_1_encodable
from schemathesis.experimental import COVERAGE_PHASE
from schemathesis.generation import GenerationConfig, GenerationMode, coverage
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE, examples, setup, strategies
from schemathesis.generation.hypothesis.given import GivenInput
from schemathesis.generation.meta import CaseMetadata, CoveragePhaseData, GenerationInfo, PhaseInfo
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, HookDispatcherMark
from schemathesis.models import APIOperation, Case
from schemathesis.parameters import ParameterSet
from schemathesis.schemas import BaseSchema

setup()


def get_all_tests(
    schema: BaseSchema,
    func: Callable,
    settings: hypothesis.settings | None = None,
    generation_config: GenerationConfig | None = None,
    seed: int | None = None,
    as_strategy_kwargs: dict[str, Any] | Callable[[APIOperation], dict[str, Any]] | None = None,
    _given_kwargs: dict[str, GivenInput] | None = None,
) -> Generator[Result[tuple[APIOperation, Callable], InvalidSchema], None, None]:
    """Generate all operations and Hypothesis tests for them."""
    for result in schema.get_all_operations(generation_config=generation_config):
        if isinstance(result, Ok):
            operation = result.ok()
            _as_strategy_kwargs: dict[str, Any] | None
            if callable(as_strategy_kwargs):
                _as_strategy_kwargs = as_strategy_kwargs(operation)
            else:
                _as_strategy_kwargs = as_strategy_kwargs
            test = create_test(
                operation=operation,
                test=func,
                settings=settings,
                seed=seed,
                generation_config=generation_config,
                as_strategy_kwargs=_as_strategy_kwargs,
                _given_kwargs=_given_kwargs,
            )
            yield Ok((operation, test))
        else:
            yield result


def create_test(
    *,
    operation: APIOperation,
    test: Callable,
    settings: hypothesis.settings | None = None,
    seed: int | None = None,
    generation_config: GenerationConfig | None = None,
    as_strategy_kwargs: dict[str, Any] | None = None,
    keep_async_fn: bool = False,
    _given_args: tuple[GivenInput, ...] = (),
    _given_kwargs: dict[str, GivenInput] | None = None,
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = HookDispatcherMark.get(test)
    auth_storage = AuthStorageMark.get(test)
    _strategies = []
    generation_config = generation_config or operation.schema.generation_config

    skip_on_not_negated = len(generation_config.modes) == 1 and GenerationMode.NEGATIVE in generation_config.modes
    as_strategy_kwargs = as_strategy_kwargs or {}
    as_strategy_kwargs.update(
        {
            "hooks": hook_dispatcher,
            "auth_storage": auth_storage,
            "generation_config": generation_config,
            "skip_on_not_negated": skip_on_not_negated,
        }
    )
    for mode in generation_config.modes:
        _strategies.append(operation.as_strategy(generation_mode=mode, **as_strategy_kwargs))
    strategy = strategies.combine(_strategies)
    _given_kwargs = (_given_kwargs or {}).copy()
    _given_kwargs.setdefault("case", strategy)

    # Each generated test should be a unique function. It is especially important for the case when Schemathesis runs
    # tests in multiple threads because Hypothesis stores some internal attributes on function objects and re-writing
    # them from different threads may lead to unpredictable side-effects.

    @wraps(test)
    def test_function(*args: Any, **kwargs: Any) -> Any:
        __tracebackhide__ = True
        return test(*args, **kwargs)

    wrapped_test = hypothesis.given(*_given_args, **_given_kwargs)(test_function)
    if seed is not None:
        wrapped_test = hypothesis.seed(seed)(wrapped_test)
    if asyncio.iscoroutinefunction(test):
        # `pytest-trio` expects a coroutine function
        if keep_async_fn:
            wrapped_test.hypothesis.inner_test = test  # type: ignore
        else:
            wrapped_test.hypothesis.inner_test = make_async_test(test)  # type: ignore
    setup_default_deadline(wrapped_test)
    if settings is not None:
        existing_settings = _get_hypothesis_settings(wrapped_test)
        if existing_settings is not None:
            # Merge the user-provided settings with the current ones
            default = hypothesis.settings.default
            wrapped_test._hypothesis_internal_use_settings = hypothesis.settings(
                wrapped_test._hypothesis_internal_use_settings,
                **{item: value for item, value in settings.__dict__.items() if value != getattr(default, item)},
            )
        else:
            wrapped_test = settings(wrapped_test)
    existing_settings = _get_hypothesis_settings(wrapped_test)
    if existing_settings is not None:
        existing_settings = remove_explain_phase(existing_settings)
        wrapped_test._hypothesis_internal_use_settings = existing_settings  # type: ignore
        if Phase.explicit in existing_settings.phases:
            wrapped_test = add_examples(
                wrapped_test, operation, hook_dispatcher=hook_dispatcher, as_strategy_kwargs=as_strategy_kwargs
            )
            if COVERAGE_PHASE.is_enabled:
                wrapped_test = add_coverage(wrapped_test, operation, generation_config.modes)
    return wrapped_test


def setup_default_deadline(wrapped_test: Callable) -> None:
    # Quite hacky, but it is the simplest way to set up the default deadline value without affecting non-Schemathesis
    # tests globally
    existing_settings = _get_hypothesis_settings(wrapped_test)
    if existing_settings is not None and existing_settings.deadline == hypothesis.settings.default.deadline:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", HypothesisWarning)
            new_settings = hypothesis.settings(existing_settings, deadline=DEFAULT_DEADLINE)
        wrapped_test._hypothesis_internal_use_settings = new_settings  # type: ignore


def remove_explain_phase(settings: hypothesis.settings) -> hypothesis.settings:
    # The "explain" phase is not supported
    if Phase.explain in settings.phases:
        phases = tuple(phase for phase in settings.phases if phase != Phase.explain)
        return hypothesis.settings(settings, phases=phases)
    return settings


def _get_hypothesis_settings(test: Callable) -> hypothesis.settings | None:
    return getattr(test, "_hypothesis_internal_use_settings", None)


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
    hook_dispatcher: HookDispatcher | None = None,
    as_strategy_kwargs: dict[str, Any] | None = None,
) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

    try:
        result: list[Case] = [
            examples.generate_one(strategy)
            for strategy in operation.get_strategies_from_examples(as_strategy_kwargs=as_strategy_kwargs)
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
    context = HookContext(operation)  # context should be passed here instead
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
        if example.media_type is not None:
            try:
                media_type = media_types.parse(example.media_type)
                if media_type == ("application", "x-www-form-urlencoded"):
                    example.body = prepare_urlencoded(example.body)
            except ValueError:
                pass
        test = hypothesis.example(case=example)(test)
    return test


def add_coverage(test: Callable, operation: APIOperation, generation_modes: list[GenerationMode]) -> Callable:
    for example in _iter_coverage_cases(operation, generation_modes):
        test = hypothesis.example(case=example)(test)
    return test


class Instant:
    __slots__ = ("start",)

    def __init__(self) -> None:
        self.start = perf_counter()

    @property
    def elapsed(self) -> float:
        return perf_counter() - self.start


def _iter_coverage_cases(
    operation: APIOperation, generation_modes: list[GenerationMode]
) -> Generator[Case, None, None]:
    from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
    from schemathesis.specs.openapi.examples import find_in_responses, find_matching_in_responses

    def _stringify_value(val: Any, location: str) -> str | list[str]:
        if isinstance(val, list):
            if location == "query":
                # Having a list here ensures there will be multiple query parameters wit the same name
                return [json.dumps(item) for item in val]
            # use comma-separated values style for arrays
            return ",".join(json.dumps(sub) for sub in val)
        return json.dumps(val)

    generators: dict[tuple[str, str], Generator[coverage.GeneratedValue, None, None]] = {}
    template: dict[str, Any] = {}

    instant = Instant()
    responses = find_in_responses(operation)
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
        container = template.setdefault(LOCATION_TO_CONTAINER[location], {})
        if location in ("header", "cookie", "path", "query") and not isinstance(value.value, str):
            container[name] = _stringify_value(value.value, location)
        else:
            container[name] = value.value
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
                template["body"] = value.value
                template["media_type"] = body.media_type
            yield operation.Case(
                **{**template, "body": value.value, "media_type": body.media_type},
                meta=CaseMetadata(
                    generation=GenerationInfo(
                        time=elapsed,
                        mode=value.generation_mode,
                    ),
                    components={},
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
                    yield operation.Case(
                        **{**template, "body": next_value.value, "media_type": body.media_type},
                        meta=CaseMetadata(
                            generation=GenerationInfo(
                                time=instant.elapsed,
                                mode=value.generation_mode,
                            ),
                            components={},
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
        yield operation.Case(
            **template,
            meta=CaseMetadata(
                generation=GenerationInfo(
                    time=template_time,
                    mode=GenerationMode.POSITIVE,
                ),
                components={},
                phase=PhaseInfo.coverage(description="Default positive test case"),
            ),
        )

    for (location, name), gen in generators.items():
        container_name = LOCATION_TO_CONTAINER[location]
        container = template[container_name]
        iterator = iter(gen)
        while True:
            instant = Instant()
            try:
                value = next(iterator)
                if location in ("header", "cookie", "path", "query") and not isinstance(value.value, str):
                    generated = _stringify_value(value.value, location)
                else:
                    generated = value.value
            except StopIteration:
                break
            yield operation.Case(
                **{**template, container_name: {**container, name: generated}},
                meta=CaseMetadata(
                    generation=GenerationInfo(time=instant.elapsed, mode=value.generation_mode),
                    components={},
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
        methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"} - set(
            operation.schema[operation.path]
        )
        for method in methods:
            instant = Instant()
            yield operation.Case(
                **template,
                method=method.upper(),
                meta=CaseMetadata(
                    generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                    components={},
                    phase=PhaseInfo.coverage(description=f"Unspecified HTTP method: {method}"),
                ),
            )
        # Generate duplicate query parameters
        if operation.query:
            container = template["query"]
            for parameter in operation.query:
                instant = Instant()
                value = container[parameter.name]
                yield operation.Case(
                    **{**template, "query": {**container, parameter.name: [value, value]}},
                    meta=CaseMetadata(
                        generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                        components={},
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
                yield operation.Case(
                    **{**template, container_name: {k: v for k, v in container.items() if k != name}},
                    meta=CaseMetadata(
                        generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                        components={},
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
            if _location in ("header", "cookie", "path", "query"):
                container = {
                    name: _stringify_value(val, _location) if not isinstance(val, str) else val
                    for name, val in container_values.items()
                }
            else:
                container = container_values

            return operation.Case(
                **{**template, _container_name: container},
                meta=CaseMetadata(
                    generation=GenerationInfo(
                        time=_instant.elapsed,
                        mode=_generation_mode,
                    ),
                    components={},
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


def find_invalid_headers(headers: Mapping) -> Generator[tuple[str, str], None, None]:
    for name, value in headers.items():
        if not is_latin_1_encodable(value) or has_invalid_characters(name, value):
            yield name, value


UnsatisfiableExampleMark = Mark[Unsatisfiable](attr_name="unsatisfiable_example")
NonSerializableMark = Mark[SerializationNotPossible](attr_name="non_serializable")
InvalidRegexMark = Mark[SchemaError](attr_name="invalid_regex")
InvalidHeadersExampleMark = Mark[dict[str, str]](attr_name="invalid_example_header")
