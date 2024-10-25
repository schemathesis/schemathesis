"""High-level API for creating Hypothesis tests."""

from __future__ import annotations

import asyncio
import json
import warnings
from functools import wraps
from itertools import combinations
from typing import TYPE_CHECKING, Any, Callable, Generator, Mapping

import hypothesis
from hypothesis import Phase
from hypothesis.errors import HypothesisWarning, Unsatisfiable
from hypothesis.internal.entropy import deterministic_PRNG
from jsonschema.exceptions import SchemaError

from . import _patches
from .auths import get_auth_storage_from_test
from .constants import DEFAULT_DEADLINE, NOT_SET
from .exceptions import OperationSchemaError, SerializationNotPossible
from .experimental import COVERAGE_PHASE
from .generation import DataGenerationMethod, GenerationConfig, combine_strategies, coverage, get_single_example
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from .models import APIOperation, Case, GenerationMetadata, TestPhase
from .transports.content_types import parse_content_type
from .transports.headers import has_invalid_characters, is_latin_1_encodable
from .types import NotSet

if TYPE_CHECKING:
    from .utils import GivenInput

# Forcefully initializes Hypothesis' global PRNG to avoid races that initialize it
# if e.g. Schemathesis CLI is used with multiple workers
with deterministic_PRNG():
    pass

_patches.install()


def create_test(
    *,
    operation: APIOperation,
    test: Callable,
    settings: hypothesis.settings | None = None,
    seed: int | None = None,
    data_generation_methods: list[DataGenerationMethod],
    generation_config: GenerationConfig | None = None,
    as_strategy_kwargs: dict[str, Any] | None = None,
    keep_async_fn: bool = False,
    _given_args: tuple[GivenInput, ...] = (),
    _given_kwargs: dict[str, GivenInput] | None = None,
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = getattr(test, "_schemathesis_hooks", None)
    auth_storage = get_auth_storage_from_test(test)
    strategies = []
    skip_on_not_negated = len(data_generation_methods) == 1 and DataGenerationMethod.negative in data_generation_methods
    as_strategy_kwargs = as_strategy_kwargs or {}
    as_strategy_kwargs.update(
        {
            "hooks": hook_dispatcher,
            "auth_storage": auth_storage,
            "generation_config": generation_config,
            "skip_on_not_negated": skip_on_not_negated,
        }
    )
    for data_generation_method in data_generation_methods:
        strategies.append(operation.as_strategy(data_generation_method=data_generation_method, **as_strategy_kwargs))
    strategy = combine_strategies(strategies)
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
                wrapped_test = add_coverage(wrapped_test, operation, data_generation_methods)
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
        examples: list[Case] = [
            get_single_example(strategy)
            for strategy in operation.get_strategies_from_examples(as_strategy_kwargs=as_strategy_kwargs)
        ]
    except (
        OperationSchemaError,
        HypothesisRefResolutionError,
        Unsatisfiable,
        SerializationNotPossible,
        SchemaError,
    ) as exc:
        # Invalid schema:
        # In this case, the user didn't pass `--validate-schema=false` and see an error in the output anyway,
        # and no tests will be executed. For this reason, examples can be skipped
        # Recursive references: This test will be skipped anyway
        # Unsatisfiable:
        # The underlying schema is not satisfiable and test will raise an error for the same reason.
        # Skipping this exception here allows us to continue the testing process for other operations.
        # Still, we allow running user-defined hooks
        examples = []
        if isinstance(exc, Unsatisfiable):
            add_unsatisfied_example_mark(test, exc)
        if isinstance(exc, SerializationNotPossible):
            add_non_serializable_mark(test, exc)
        if isinstance(exc, SchemaError):
            add_invalid_regex_mark(test, exc)
    context = HookContext(operation)  # context should be passed here instead
    GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, examples)
    operation.schema.hooks.dispatch("before_add_examples", context, examples)
    if hook_dispatcher:
        hook_dispatcher.dispatch("before_add_examples", context, examples)
    original_test = test
    for example in examples:
        if example.headers is not None:
            invalid_headers = dict(find_invalid_headers(example.headers))
            if invalid_headers:
                add_invalid_example_header_mark(original_test, invalid_headers)
                continue
        if example.media_type is not None:
            try:
                media_type = parse_content_type(example.media_type)
                if media_type == ("application", "x-www-form-urlencoded"):
                    example.body = prepare_urlencoded(example.body)
            except ValueError:
                pass
        test = hypothesis.example(case=example)(test)
    return test


def add_coverage(
    test: Callable, operation: APIOperation, data_generation_methods: list[DataGenerationMethod]
) -> Callable:
    for example in _iter_coverage_cases(operation, data_generation_methods):
        test = hypothesis.example(case=example)(test)
    return test


def _iter_coverage_cases(
    operation: APIOperation, data_generation_methods: list[DataGenerationMethod]
) -> Generator[Case, None, None]:
    from .specs.openapi.constants import LOCATION_TO_CONTAINER
    from .specs.openapi.examples import find_in_responses, find_matching_in_responses

    generators: dict[tuple[str, str], Generator[coverage.GeneratedValue, None, None]] = {}
    template: dict[str, Any] = {}
    responses = find_in_responses(operation)
    for parameter in operation.iter_parameters():
        schema = parameter.as_json_schema(operation, update_quantifiers=False)
        for value in find_matching_in_responses(responses, parameter.name):
            schema.setdefault("examples", []).append(value)
        gen = coverage.cover_schema_iter(
            coverage.CoverageContext(data_generation_methods=data_generation_methods), schema
        )
        value = next(gen, NOT_SET)
        if isinstance(value, NotSet):
            continue
        location = parameter.location
        name = parameter.name
        container = template.setdefault(LOCATION_TO_CONTAINER[location], {})
        if location in ("header", "cookie") and not isinstance(value.value, str):
            container[name] = json.dumps(value.value)
        else:
            container[name] = value.value
        generators[(location, name)] = gen
    if operation.body:
        for body in operation.body:
            schema = body.as_json_schema(operation, update_quantifiers=False)
            # Definition could be a list for Open API 2.0
            definition = body.definition if isinstance(body.definition, dict) else {}
            examples = [example["value"] for example in definition.get("examples", {}).values() if "value" in example]
            if examples:
                schema.setdefault("examples", []).extend(examples)
            gen = coverage.cover_schema_iter(
                coverage.CoverageContext(data_generation_methods=data_generation_methods), schema
            )
            value = next(gen, NOT_SET)
            if isinstance(value, NotSet):
                continue
            if "body" not in template:
                template["body"] = value.value
                template["media_type"] = body.media_type
            case = operation.make_case(**{**template, "body": value.value, "media_type": body.media_type})
            case.data_generation_method = value.data_generation_method
            case.meta = _make_meta(
                description=value.description,
                location=value.location,
                parameter=body.media_type,
                parameter_location="body",
            )
            yield case
            for next_value in gen:
                case = operation.make_case(**{**template, "body": next_value.value, "media_type": body.media_type})
                case.data_generation_method = next_value.data_generation_method
                case.meta = _make_meta(
                    description=next_value.description,
                    location=next_value.location,
                    parameter=body.media_type,
                    parameter_location="body",
                )
                yield case
    elif DataGenerationMethod.positive in data_generation_methods:
        case = operation.make_case(**template)
        case.data_generation_method = DataGenerationMethod.positive
        case.meta = _make_meta()
        yield case
    for (location, name), gen in generators.items():
        container_name = LOCATION_TO_CONTAINER[location]
        container = template[container_name]
        for value in gen:
            if location in ("header", "cookie") and not isinstance(value.value, str):
                generated = json.dumps(value.value)
            else:
                generated = value.value
            case = operation.make_case(**{**template, container_name: {**container, name: generated}})
            case.data_generation_method = value.data_generation_method
            case.meta = _make_meta(
                description=value.description,
                location=value.location,
                parameter=name,
                parameter_location=location,
            )
            yield case
    # Generate missing required parameters
    if DataGenerationMethod.negative in data_generation_methods:
        for parameter in operation.iter_parameters():
            if parameter.is_required and parameter.location != "path":
                name = parameter.name
                location = parameter.location
                container_name = LOCATION_TO_CONTAINER[location]
                container = template[container_name]
                case = operation.make_case(
                    **{**template, container_name: {k: v for k, v in container.items() if k != name}}
                )
                case.data_generation_method = DataGenerationMethod.negative
                case.meta = _make_meta(
                    description=f"Missing `{name}` at {location}",
                    location=parameter.location,
                    parameter=name,
                    parameter_location=location,
                )
                yield case
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
        def make_case(container_values: dict, description: str, _location: str, _container_name: str) -> Case:
            if _location in ("header", "cookie"):
                container = {
                    name: json.dumps(val) if not isinstance(val, str) else val for name, val in container_values.items()
                }
            else:
                container = container_values

            case = operation.make_case(**{**template, _container_name: container})
            case.data_generation_method = DataGenerationMethod.positive
            case.meta = _make_meta(
                description=description,
                location=_location,
                parameter_location=_location,
            )
            return case

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            yield make_case(only_required, "Only required properties", location, container_name)

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container:
                yield make_case(combo, f"All required properties and optional '{opt_param}'", location, container_name)

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if len(optional) > 1:
            for size in range(2, len(optional)):
                for combination in combinations(optional, size):
                    combo = {k: v for k, v in base_container.items() if k in required or k in combination}
                    if combo != base_container:
                        yield make_case(combo, f"All required and {size} optional properties", location, container_name)


def _make_meta(
    *,
    description: str | None = None,
    location: str | None = None,
    parameter: str | None = None,
    parameter_location: str | None = None,
) -> GenerationMetadata:
    return GenerationMetadata(
        query=None,
        path_parameters=None,
        headers=None,
        cookies=None,
        body=None,
        phase=TestPhase.COVERAGE,
        description=description,
        location=location,
        parameter=parameter,
        parameter_location=parameter_location,
    )


def find_invalid_headers(headers: Mapping) -> Generator[tuple[str, str], None, None]:
    for name, value in headers.items():
        if not is_latin_1_encodable(value) or has_invalid_characters(name, value):
            yield name, value


def prepare_urlencoded(data: Any) -> Any:
    if isinstance(data, list):
        output = []
        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    output.append((key, value))
            else:
                output.append(item)
        return output
    return data


def add_unsatisfied_example_mark(test: Callable, exc: Unsatisfiable) -> None:
    test._schemathesis_unsatisfied_example = exc  # type: ignore


def has_unsatisfied_example_mark(test: Callable) -> bool:
    return hasattr(test, "_schemathesis_unsatisfied_example")


def add_non_serializable_mark(test: Callable, exc: SerializationNotPossible) -> None:
    test._schemathesis_non_serializable = exc  # type: ignore


def get_non_serializable_mark(test: Callable) -> SerializationNotPossible | None:
    return getattr(test, "_schemathesis_non_serializable", None)


def get_invalid_regex_mark(test: Callable) -> SchemaError | None:
    return getattr(test, "_schemathesis_invalid_regex", None)


def add_invalid_regex_mark(test: Callable, exc: SchemaError) -> None:
    test._schemathesis_invalid_regex = exc  # type: ignore


def get_invalid_example_headers_mark(test: Callable) -> dict[str, str] | None:
    return getattr(test, "_schemathesis_invalid_example_headers", None)


def add_invalid_example_header_mark(test: Callable, headers: dict[str, str]) -> None:
    test._schemathesis_invalid_example_headers = headers  # type: ignore
