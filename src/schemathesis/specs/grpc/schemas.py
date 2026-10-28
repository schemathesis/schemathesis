from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass, field
from difflib import get_close_matches
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn, Self, cast

from google.protobuf import descriptor_pool
from typing_extensions import override

from schemathesis.core import NOT_SET, Body, NotSet, Specification
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.result import Ok, Result
from schemathesis.core.statistic import ApiStatistic
from schemathesis.filters import FilterUsage
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import NO_FEEDBACK, FeedbackSources
from schemathesis.generation.meta import TestPhase
from schemathesis.schemas import APIOperation, APIOperationMap, BaseSchema, OperationDefinition, UndeclaredResponses

if TYPE_CHECKING:
    from google.protobuf.descriptor import MethodDescriptor, ServiceDescriptor
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.spec import ApiSchema
    from schemathesis.engine.link_calibration import LinkCalibrationState
    from schemathesis.filters import FilterSet
    from schemathesis.generation.case import Case
    from schemathesis.generation.coverage import GenerationSession
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.hooks import HookDispatcher
    from schemathesis.resources import ResourcePool


def is_unary(method: MethodDescriptor) -> bool:
    return not (method.client_streaming or method.server_streaming)


@dataclass(eq=False, repr=False)
class GrpcSchema(BaseSchema):
    # Built from the serialized descriptor set, which keeps extensions that `raw_schema` drops.
    pool: descriptor_pool.DescriptorPool = field(default_factory=descriptor_pool.DescriptorPool)
    # Tested services by full name, in declaration order.
    services: dict[str, ServiceDescriptor] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"

    @override
    def clone(self, *, test_function: Callable | NotSet = NOT_SET, filter_set: FilterSet | NotSet = NOT_SET) -> Self:
        cloned = super().clone(test_function=test_function, filter_set=filter_set)
        # Descriptors never change, so clones share them.
        cloned.pool = self.pool
        cloned.services = self.services
        return cloned

    @property
    @override
    def specification(self) -> Specification:
        return Specification.grpc(version="")

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.services)

    @override
    def _get_operation_map(self, key: str) -> APIOperationMap:
        service = self.services[key]
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, service, {method.name: method for method in service.methods if is_unary(method)})
        return map

    @override
    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        message = f"`{item}` service not found"
        matches = get_close_matches(item, list(self.services))
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    @override
    def find_operation_by_label(self, label: str) -> APIOperation | None:
        service_name, _, method_name = label.partition("/")
        service = self.services.get(service_name)
        if service is None:
            return None
        method = service.methods_by_name.get(method_name)
        if method is None or not is_unary(method):
            return None
        return self._build_operation(method)

    @override
    def declared_base_path(self) -> str:
        return ""

    @override
    def validate(self) -> None:
        return None

    @override
    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        raise NotImplementedError

    @override
    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return [operation.definition.raw.containing_service.full_name]

    @override
    def get_operation_id(self, operation: APIOperation) -> str | None:
        return None

    @override
    def is_deprecated(self, operation: APIOperation) -> bool:
        method = operation.definition.raw
        service = method.containing_service
        return method.GetOptions().deprecated or service.GetOptions().deprecated or service.file.GetOptions().deprecated

    @override
    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()
        usage = FilterUsage(self.filter_set) if not self.filter_set.is_empty() else None
        for service in self.services.values():
            for method in service.methods:
                statistic.operations.total += 1
                operation = self._build_operation(method)
                if is_unary(method) and not self._should_skip(operation):
                    statistic.operations.selected += 1
                # A filter naming a streaming method matched something, even though it is not tested.
                if usage is not None:
                    usage.record(SimpleNamespace(operation=operation))
        if usage is not None:
            statistic.unmatched_filters = usage.results()
        return statistic

    @override
    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        for service in self.services.values():
            for method in service.methods:
                if is_unary(method):
                    operation = self._build_operation(method)
                    if not self._should_skip(operation):
                        yield Ok(operation)

    def _build_operation(self, method: MethodDescriptor) -> APIOperation:
        label = f"{method.containing_service.full_name}/{method.name}"
        return APIOperation(
            base_url=self.get_base_url(),
            path=f"/{label}",
            label=label,
            method="post",
            app=self.app,
            schema=self,
            responses=UndeclaredResponses(),
            security=None,
            definition=OperationDefinition(raw=method),
        )

    @override
    def _case_media_type(self, operation: APIOperation, body: Body, media_type: str | None) -> str | None:
        raise NotImplementedError

    @override
    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        feedback: FeedbackSources = NO_FEEDBACK,
        *,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = NOT_SET,
        media_type: str | None = None,
        phase: TestPhase = TestPhase.FUZZING,
    ) -> SearchStrategy[Case]:
        raise NotImplementedError

    @override
    def get_strategies_from_examples(
        self, operation: APIOperation, feedback: FeedbackSources = NO_FEEDBACK, **kwargs: Any
    ) -> list[SearchStrategy[Case]]:
        raise NotImplementedError

    @override
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ResourcePool | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
        session: GenerationSession | None = None,
    ) -> Iterator[Case]:
        raise NotImplementedError

    @override
    def as_state_machine(self) -> type[APIStateMachine]:
        raise NotImplementedError

    @override
    def build_state_machine(
        self,
        *,
        feedback: FeedbackSources,
        link_calibration: LinkCalibrationState | None,
    ) -> type[APIStateMachine]:
        raise NotImplementedError

    @override
    def create_extra_data_source(self) -> None:
        raise NotImplementedError


@dataclass
class MethodMap(Mapping):
    _parent: APIOperationMap
    _service: ServiceDescriptor
    _unary: dict[str, MethodDescriptor]

    __slots__ = ("_parent", "_service", "_unary")

    def __len__(self) -> int:
        return len(self._unary)

    def __iter__(self) -> Iterator[str]:
        return iter(self._unary)

    def __getitem__(self, item: str) -> APIOperation:
        method = self._unary.get(item)
        if method is None:
            if item in self._service.methods_by_name:
                raise KeyError(f"`{item}` is a streaming method; only unary methods are supported")
            message = f"`{item}` method not found"
            matches = get_close_matches(item, list(self._unary))
            if matches:
                message += f". Did you mean `{matches[0]}`?"
            raise KeyError(message)
        schema = cast(GrpcSchema, self._parent._schema)
        return schema._build_operation(method)


if TYPE_CHECKING:
    # Verify structural conformance to the spec-agnostic protocol; mypy fails here
    # if a method is renamed or its signature drifts from `ApiSchema`.
    def _verify_api_schema_protocol(schema: GrpcSchema) -> ApiSchema:
        return schema
