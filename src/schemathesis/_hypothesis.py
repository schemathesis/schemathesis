"""High-level API for creating Hypothesis tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import warnings
from functools import wraps
from itertools import combinations
from typing import TYPE_CHECKING, Any, Callable, Generator, Mapping

import hypothesis
from hypothesis import Phase
from hypothesis.errors import HypothesisWarning, Unsatisfiable
from hypothesis.internal.entropy import deterministic_PRNG
from jsonschema.exceptions import SchemaError

from schemathesis.serializers import get_first_matching_media_type

from . import _patches
from .auths import AuthStorage, get_auth_storage_from_test
from .constants import DEFAULT_DEADLINE, NOT_SET
from .exceptions import OperationSchemaError, SerializationNotPossible
from .experimental import COVERAGE_PHASE
from .generation import DataGenerationMethod, GenerationConfig, combine_strategies, coverage, get_single_example
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from .models import APIOperation, Case, GenerationMetadata, TestPhase
from .parameters import ParameterSet
from .transports.content_types import parse_content_type
from .transports.headers import has_invalid_characters, is_latin_1_encodable
from .types import NotSet
from schemathesis import auths

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
                unexpected_methods = generation_config.unexpected_methods if generation_config else None
                wrapped_test = add_coverage(
                    wrapped_test,
                    operation,
                    data_generation_methods,
                    auth_storage,
                    as_strategy_kwargs,
                    unexpected_methods,
                )
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
        adjust_urlencoded_payload(example)
        test = hypothesis.example(case=example)(test)
    return test


def adjust_urlencoded_payload(case: Case) -> None:
    if case.media_type is not None:
        try:
            media_type = parse_content_type(case.media_type)
            if media_type == ("application", "x-www-form-urlencoded"):
                case.body = prepare_urlencoded(case.body)
        except ValueError:
            pass


def add_coverage(
    test: Callable,
    operation: APIOperation,
    data_generation_methods: list[DataGenerationMethod],
    auth_storage: AuthStorage | None,
    as_strategy_kwargs: dict[str, Any],
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
    for case in _iter_coverage_cases(operation, data_generation_methods, unexpected_methods):
        if case.media_type and get_first_matching_media_type(case.media_type) is None:
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


class Template:
    __slots__ = ("_components", "_template", "_serializers")

    def __init__(self, serializers: dict[str, Callable]) -> None:
        self._components: dict[str, DataGenerationMethod] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers

    def __contains__(self, key: str) -> bool:
        return key in self._template

    def __getitem__(self, key: str) -> dict:
        return self._template[key]

    def get(self, key: str, default: Any = None) -> dict:
        return self._template.get(key, default)

    def add_parameter(self, location: str, name: str, value: coverage.GeneratedValue) -> None:
        from .specs.openapi.constants import LOCATION_TO_CONTAINER

        component_name = LOCATION_TO_CONTAINER[location]
        method = self._components.get(component_name)
        if method is None:
            self._components[component_name] = value.data_generation_method
        elif value.data_generation_method == DataGenerationMethod.negative:
            self._components[component_name] = DataGenerationMethod.negative

        container = self._template.setdefault(component_name, {})
        container[name] = value.value

    def set_body(self, body: coverage.GeneratedValue, media_type: str) -> None:
        self._template["body"] = body.value
        self._template["media_type"] = media_type
        self._components["body"] = body.data_generation_method

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
        components = {**self._components, "body": value.data_generation_method}
        return TemplateValue(kwargs=kwargs, components=components)

    def with_parameter(self, *, location: str, name: str, value: coverage.GeneratedValue) -> TemplateValue:
        from .specs.openapi.constants import LOCATION_TO_CONTAINER

        container_name = LOCATION_TO_CONTAINER[location]
        container = self._template[container_name]
        return self.with_container(
            container_name=container_name,
            value={**container, name: value.value},
            data_generation_method=value.data_generation_method,
        )

    def with_container(
        self, *, container_name: str, value: Any, data_generation_method: DataGenerationMethod
    ) -> TemplateValue:
        kwargs = {**self._template, container_name: value}
        kwargs = self._serialize(kwargs)
        components = {**self._components, container_name: data_generation_method}
        return TemplateValue(kwargs=kwargs, components=components)


@dataclass
class TemplateValue:
    kwargs: dict[str, Any]
    components: dict[str, DataGenerationMethod]
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
        return ",".join(_stringify_value(sub, container_name) for sub in val)
    if isinstance(val, dict):
        return {key: _stringify_value(sub, container_name) for key, sub in val.items()}
    return val


def _iter_coverage_cases(
    operation: APIOperation,
    data_generation_methods: list[DataGenerationMethod],
    unexpected_methods: set[str] | None = None,
) -> Generator[Case, None, None]:
    from .specs.openapi.constants import LOCATION_TO_CONTAINER
    from .specs.openapi.examples import find_in_responses, find_matching_in_responses
    from schemathesis.specs.openapi.serialization import get_serializers_for_operation

    generators: dict[tuple[str, str], Generator[coverage.GeneratedValue, None, None]] = {}
    serializers = get_serializers_for_operation(operation)
    template = Template(serializers)
    responses = find_in_responses(operation)
    # NOTE: The HEAD method is excluded
    unexpected_methods = unexpected_methods or {"get", "put", "post", "delete", "options", "patch", "trace"}
    for parameter in operation.iter_parameters():
        location = parameter.location
        name = parameter.name
        schema = parameter.as_json_schema(operation, update_quantifiers=False)
        for value in find_matching_in_responses(responses, parameter.name):
            schema.setdefault("examples", []).append(value)
        gen = coverage.cover_schema_iter(
            coverage.CoverageContext(location=location, data_generation_methods=data_generation_methods), schema
        )
        value = next(gen, NOT_SET)
        if isinstance(value, NotSet):
            continue
        template.add_parameter(location, name, value)
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
                coverage.CoverageContext(location="body", data_generation_methods=data_generation_methods), schema
            )
            value = next(gen, NOT_SET)
            if isinstance(value, NotSet):
                continue
            if "body" not in template:
                template.set_body(value, body.media_type)
            data = template.with_body(value=value, media_type=body.media_type)
            case = operation.make_case(**data.kwargs)
            case.data_generation_method = value.data_generation_method
            case.meta = _make_meta(
                description=value.description,
                location=value.location,
                parameter=body.media_type,
                parameter_location="body",
                **data.components,
            )
            yield case
            for next_value in gen:
                data = template.with_body(value=next_value, media_type=body.media_type)
                case = operation.make_case(**data.kwargs)
                case.data_generation_method = next_value.data_generation_method
                case.meta = _make_meta(
                    description=next_value.description,
                    location=next_value.location,
                    parameter=body.media_type,
                    parameter_location="body",
                    **data.components,
                )
                yield case
    elif DataGenerationMethod.positive in data_generation_methods:
        data = template.unmodified()
        case = operation.make_case(**data.kwargs)
        case.data_generation_method = DataGenerationMethod.positive
        case.meta = _make_meta(description="Default positive test case", **data.components)
        yield case

    for (location, name), gen in generators.items():
        for value in gen:
            data = template.with_parameter(location=location, name=name, value=value)
            case = operation.make_case(**data.kwargs)
            case.data_generation_method = value.data_generation_method
            case.meta = _make_meta(
                description=value.description,
                location=value.location,
                parameter=name,
                parameter_location=location,
                **data.components,
            )
            yield case
    if DataGenerationMethod.negative in data_generation_methods:
        # Generate HTTP methods that are not specified in the spec
        methods = unexpected_methods - set(operation.schema[operation.path])
        for method in sorted(methods):
            data = template.unmodified()
            case = operation.make_case(**data.kwargs)
            case._explicit_method = method
            case.data_generation_method = DataGenerationMethod.negative
            case.meta = _make_meta(description=f"Unspecified HTTP method: {method.upper()}", **data.components)
            yield case
        # Generate duplicate query parameters
        if operation.query:
            container = template["query"]
            for parameter in operation.query:
                # Could be absent if value schema can't be negated
                # I.e. contains just `default` value without any other keywords
                value = container.get(parameter.name, NOT_SET)
                if value is not NOT_SET:
                    data = template.with_container(
                        container_name="query",
                        value={**container, parameter.name: [value, value]},
                        data_generation_method=DataGenerationMethod.negative,
                    )
                    case = operation.make_case(**data.kwargs)
                    case.data_generation_method = DataGenerationMethod.negative
                    case.meta = _make_meta(
                        description=f"Duplicate `{parameter.name}` query parameter",
                        location=None,
                        parameter=parameter.name,
                        parameter_location="query",
                        **data.components,
                    )
                    yield case
        # Generate missing required parameters
        for parameter in operation.iter_parameters():
            if parameter.is_required and parameter.location != "path":
                name = parameter.name
                location = parameter.location
                container_name = LOCATION_TO_CONTAINER[location]
                container = template[container_name]
                data = template.with_container(
                    container_name=container_name,
                    value={k: v for k, v in container.items() if k != name},
                    data_generation_method=DataGenerationMethod.negative,
                )
                case = operation.make_case(**data.kwargs)
                case.data_generation_method = DataGenerationMethod.negative
                case.meta = _make_meta(
                    description=f"Missing `{name}` at {location}",
                    location=None,
                    parameter=name,
                    parameter_location=location,
                    **data.components,
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
        def make_case(
            container_values: dict,
            description: str,
            _location: str,
            _container_name: str,
            _parameter: str | None,
            _data_generation_method: DataGenerationMethod,
        ) -> Case:
            data = template.with_container(
                container_name=_container_name, value=container_values, data_generation_method=_data_generation_method
            )
            case = operation.make_case(**data.kwargs)
            case.data_generation_method = _data_generation_method
            case.meta = _make_meta(
                description=description,
                location=None,
                parameter=_parameter,
                parameter_location=_location,
                **data.components,
            )
            return case

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
            for more in coverage.cover_schema_iter(
                coverage.CoverageContext(location=_location, data_generation_methods=[DataGenerationMethod.negative]),
                subschema,
            ):
                yield make_case(
                    more.value,
                    more.description,
                    _location,
                    _container_name,
                    more.parameter,
                    DataGenerationMethod.negative,
                )

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            if DataGenerationMethod.positive in data_generation_methods:
                yield make_case(
                    only_required,
                    "Only required properties",
                    location,
                    container_name,
                    None,
                    DataGenerationMethod.positive,
                )
            if DataGenerationMethod.negative in data_generation_methods:
                subschema = _combination_schema(only_required, required, parameter_set)
                for case in _yield_negative(subschema, location, container_name):
                    # Already generated in one of the blocks above
                    if location != "path" and not case.meta.description.startswith("Missing required property"):
                        yield case

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container and DataGenerationMethod.positive in data_generation_methods:
                yield make_case(
                    combo,
                    f"All required properties and optional '{opt_param}'",
                    location,
                    container_name,
                    None,
                    DataGenerationMethod.positive,
                )
                if DataGenerationMethod.negative in data_generation_methods:
                    subschema = _combination_schema(combo, required, parameter_set)
                    for case in _yield_negative(subschema, location, container_name):
                        # Already generated in one of the blocks above
                        if location != "path" and not case.meta.description.startswith("Missing required property"):
                            yield case

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if len(optional) > 1 and DataGenerationMethod.positive in data_generation_methods:
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
                            DataGenerationMethod.positive,
                        )


def _make_meta(
    *,
    description: str,
    location: str | None = None,
    parameter: str | None = None,
    parameter_location: str | None = None,
    query: DataGenerationMethod | None = None,
    path_parameters: DataGenerationMethod | None = None,
    headers: DataGenerationMethod | None = None,
    cookies: DataGenerationMethod | None = None,
    body: DataGenerationMethod | None = None,
) -> GenerationMetadata:
    return GenerationMetadata(
        query=query,
        path_parameters=path_parameters,
        headers=headers,
        cookies=cookies,
        body=body,
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
                output.append((item, "arbitrary-value"))
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
