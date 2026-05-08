from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hypothesis.errors import Unsatisfiable
from jsonschema_rs import ValidationError

from schemathesis import auths
from schemathesis.config import GenerationConfig
from schemathesis.core import INJECTED_PATH_PARAMETER_KEY
from schemathesis.core.errors import (
    SERIALIZERS_SUGGESTION_MESSAGE,
    InfiniteRecursiveReference,
    InvalidHeadersExample,
    InvalidRegexPattern,
    InvalidSchema,
    SerializationNotPossible,
    UnresolvableReference,
    is_regex_validation_error,
)
from schemathesis.core.parameters import LOCATION_TO_CONTAINER
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import (
    _iter_coverage_cases,
    adjust_urlencoded_payload,
    find_invalid_headers,
)
from schemathesis.generation.hypothesis.examples import add_single_example, generate_one
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, _should_skip_hook

if TYPE_CHECKING:
    from schemathesis.auths import AuthStorage
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


@dataclass(slots=True)
class Controller:
    """Sidecar surface for a case generator.

    Holds `deferred_errors` for pre-iteration failures the engine surfaces after
    iteration ends.
    """

    deferred_errors: list[Exception] = field(default_factory=list)


def _capture_missing_path_parameters(operation: APIOperation, controller: Controller) -> None:
    # Schema-injected path parameter placeholders surface as a deferred schema error.
    injected = [
        parameter.name
        for parameter in operation.path_parameters
        if parameter.definition.get(INJECTED_PATH_PARAMETER_KEY)
    ]
    if not injected:
        return
    names = ", ".join(f"'{name}'" for name in injected)
    plural = "s" if len(injected) > 1 else ""
    verb = "are" if len(injected) > 1 else "is"
    controller.deferred_errors.append(InvalidSchema(f"Path parameter{plural} {names} {verb} not defined"))


class CoverageGenerator:
    """Yield Coverage-phase cases for one operation, applying hooks, auth, and overrides."""

    def __init__(
        self,
        *,
        operation: APIOperation,
        generation_modes: list[GenerationMode],
        generate_duplicate_query_parameters: bool,
        unexpected_methods: set[str],
        generation_config: GenerationConfig,
        auth_storage: AuthStorage | None,
        as_strategy_kwargs: dict[str, Any],
        unexpected_methods_seen: set[tuple[str, str]] | None = None,
    ) -> None:
        self._operation = operation
        self._generation_modes = generation_modes
        self._generate_duplicate_query_parameters = generate_duplicate_query_parameters
        self._unexpected_methods = unexpected_methods
        self._generation_config = generation_config
        self._auth_storage = auth_storage
        self._as_strategy_kwargs = as_strategy_kwargs
        self._unexpected_methods_seen = unexpected_methods_seen
        self._controller = Controller()
        _capture_missing_path_parameters(operation, self._controller)

    @property
    def operation(self) -> APIOperation:
        return self._operation

    @property
    def controller(self) -> Controller:
        return self._controller

    def __iter__(self) -> Iterator[Case]:
        operation = self._operation
        as_strategy_kwargs = self._as_strategy_kwargs
        auth_context = auths.AuthContext(operation=operation, app=operation.app)
        extra_data_source = as_strategy_kwargs.get("extra_data_source")
        error_feedback = as_strategy_kwargs.get("error_feedback")
        overrides = {
            container: as_strategy_kwargs[container]
            for container in LOCATION_TO_CONTAINER.values()
            if container in as_strategy_kwargs
        }
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*but this is not valid syntax for a Python regular expression.*",
                category=UserWarning,
            )
            hook_context = HookContext(operation=operation)
            # Per-test hooks are a pytest-plugin feature; the engine has none.
            dispatchers = [GLOBAL_HOOK_DISPATCHER, operation.schema.hooks]

            for case in _iter_coverage_cases(
                operation=operation,
                generation_modes=self._generation_modes,
                generate_duplicate_query_parameters=self._generate_duplicate_query_parameters,
                unexpected_methods=self._unexpected_methods,
                generation_config=self._generation_config,
                extra_data_source=extra_data_source,
                unexpected_methods_seen=self._unexpected_methods_seen,
                error_feedback=error_feedback,
            ):
                if (
                    case.media_type
                    and operation.schema.transport.get_first_matching_media_type(case.media_type) is None
                ):
                    continue
                adjust_urlencoded_payload(case)
                auths.set_on_case(case, auth_context, self._auth_storage)
                for container_name, value in overrides.items():
                    container = getattr(case, container_name)
                    if container is None:
                        setattr(case, container_name, value)
                    else:
                        container.update(value)
                skip = False
                for dispatcher in dispatchers:
                    for hook in dispatcher.get_all_by_name("filter_case"):
                        if _should_skip_hook(hook, hook_context):
                            continue
                        if not hook(hook_context, case):
                            skip = True
                            break
                    if skip:
                        break
                if skip:
                    continue
                for dispatcher in dispatchers:
                    for hook in dispatcher.get_all_by_name("map_case"):
                        if _should_skip_hook(hook, hook_context):
                            continue
                        case = hook(hook_context, case)
                yield case


class ExamplesGenerator:
    """Yield Examples-phase cases for one operation, materialized from schema-declared examples."""

    def __init__(
        self,
        *,
        operation: APIOperation,
        as_strategy_kwargs: dict[str, Any],
        fill_missing: bool,
    ) -> None:
        self._operation = operation
        self._as_strategy_kwargs = as_strategy_kwargs
        self._fill_missing = fill_missing
        self._controller = Controller()
        _capture_missing_path_parameters(operation, self._controller)

    @property
    def operation(self) -> APIOperation:
        return self._operation

    @property
    def controller(self) -> Controller:
        return self._controller

    def _materialize_strategy_cases(self) -> list[Case]:
        operation = self._operation
        try:
            return [
                generate_one(strategy)
                for strategy in operation.get_strategies_from_examples(
                    fill_missing_from_pool=self._fill_missing,
                    **self._as_strategy_kwargs,
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
            self._defer_materialization_error(exc)
            return []

    def _defer_materialization_error(self, exc: Exception) -> None:
        translated = _translate_examples_materialization_error(exc)
        # `InvalidSchema` and non-regex `ValidationError` are silently absorbed: they
        # surface during the Coverage phase, where the schema-quality signal belongs.
        if translated is not None:
            self._controller.deferred_errors.append(translated)

    def __iter__(self) -> Iterator[Case]:
        operation = self._operation
        cases = self._materialize_strategy_cases()

        if self._fill_missing and not cases:
            try:
                add_single_example(operation.as_strategy(), cases)
            except (
                InvalidSchema,
                InfiniteRecursiveReference,
                Unsatisfiable,
                UnresolvableReference,
                SerializationNotPossible,
                ValidationError,
            ) as exc:
                self._defer_materialization_error(exc)

        context = HookContext(operation=operation)
        # Per-test hooks are a pytest-plugin feature; the engine has none.
        GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, cases)
        operation.schema.hooks.dispatch("before_add_examples", context, cases)

        for case in cases:
            if case.headers is not None:
                invalid_headers = dict(find_invalid_headers(case.headers))
                if invalid_headers:
                    self._controller.deferred_errors.append(InvalidHeadersExample.from_headers(invalid_headers))
                    continue
            adjust_urlencoded_payload(case)
            yield case


def _translate_examples_materialization_error(exc: Exception) -> Exception | None:
    """Wrap a materialization-time exception into the user-facing form, or `None` to absorb."""
    if isinstance(exc, Unsatisfiable):
        return Unsatisfiable("Failed to generate test cases from examples for this API operation")
    if isinstance(exc, SerializationNotPossible):
        media_types = ", ".join(exc.media_types)
        return SerializationNotPossible(
            "Failed to generate test cases from examples for this API operation because of"
            f" unsupported payload media types: {media_types}\n{SERIALIZERS_SUGGESTION_MESSAGE}",
            media_types=exc.media_types,
        )
    if is_regex_validation_error(exc):
        return InvalidRegexPattern.from_jsonschema_rs_error(exc)
    if isinstance(exc, (InfiniteRecursiveReference, UnresolvableReference)):
        return exc
    return None
