from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis import auths
from schemathesis.config import GenerationConfig
from schemathesis.core import INJECTED_PATH_PARAMETER_KEY
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.parameters import LOCATION_TO_CONTAINER
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import _iter_coverage_cases, adjust_urlencoded_payload
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, _should_skip_hook

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
        self._capture_missing_path_parameters()

    def _capture_missing_path_parameters(self) -> None:
        # Schema-injected path parameter placeholders surface as a deferred schema error.
        injected = [
            parameter.name
            for parameter in self._operation.path_parameters
            if parameter.definition.get(INJECTED_PATH_PARAMETER_KEY)
        ]
        if not injected:
            return
        names = ", ".join(f"'{name}'" for name in injected)
        plural = "s" if len(injected) > 1 else ""
        verb = "are" if len(injected) > 1 else "is"
        self._controller.deferred_errors.append(InvalidSchema(f"Path parameter{plural} {names} {verb} not defined"))

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
            per_test_hooks: HookDispatcher | None = as_strategy_kwargs.get("hooks")
            dispatchers = [d for d in (GLOBAL_HOOK_DISPATCHER, operation.schema.hooks, per_test_hooks) if d is not None]

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
