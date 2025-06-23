from __future__ import annotations

from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from hashlib import sha1
from json import JSONDecodeError
from threading import RLock
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Generator,
)
from urllib.parse import urlsplit

import jsonschema
from requests.exceptions import InvalidHeader
from requests.structures import CaseInsensitiveDict
from requests.utils import check_header_validity

from schemathesis.core import NOT_SET, NotSet, Specification, deserialization, media_types
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InternalError, InvalidSchema, LoaderError, LoaderErrorKind
from schemathesis.core.failures import Failure, FailureGroup, MalformedJson
from schemathesis.core.result import Ok, Result
from schemathesis.core.transforms import UNRESOLVABLE, deepclone, resolve_pointer, transform
from schemathesis.core.transport import Response
from schemathesis.core.validation import INVALID_HEADER_RE
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CaseMetadata
from schemathesis.openapi.checks import JsonSchemaError, MissingContentType
from schemathesis.specs.openapi._access import ApiOperation, OpenApi
from schemathesis.specs.openapi.utils import expand_status_code

from ...generation import GenerationMode
from ...hooks import HookDispatcher
from ...schemas import APIOperation, ApiStatistic, BaseSchema
from . import serialization
from ._hypothesis import openapi_cases
from .converter import to_json_schema
from .definitions import OPENAPI_30_VALIDATOR, OPENAPI_31_VALIDATOR, SWAGGER_20_VALIDATOR
from .examples import get_strategies_from_examples
from .parameters import OpenAPI20CompositeBody
from .references import RECURSION_DEPTH_LIMIT, ConvertingResolver, InliningResolver
from .security import BaseSecurityProcessor, OpenAPISecurityProcessor, SwaggerSecurityProcessor
from .stateful import create_state_machine

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthStorage
    from schemathesis.generation.stateful import APIStateMachine

SCHEMA_ERROR_MESSAGE = "Ensure that the definition complies with the OpenAPI specification"
SCHEMA_PARSING_ERRORS = (KeyError, AttributeError, RefResolutionError, InvalidSchema)


def check_header(parameter: dict[str, Any]) -> None:
    name = parameter["name"]
    if not name:
        raise InvalidSchema("Header name should not be empty")
    if not name.isascii():
        # `urllib3` encodes header names to ASCII
        raise InvalidSchema(f"Header name should be ASCII: {name}")
    try:
        check_header_validity((name, ""))
    except InvalidHeader as exc:
        raise InvalidSchema(str(exc)) from None
    if bool(INVALID_HEADER_RE.search(name)):
        raise InvalidSchema(f"Invalid header name: {name}")


@dataclass(eq=False, repr=False)
class BaseOpenAPISchema(BaseSchema):
    _spec: OpenApi = field(init=False)
    nullable_name: ClassVar[str] = ""
    links_field: ClassVar[str] = ""
    header_required_field: ClassVar[str] = ""
    security: ClassVar[BaseSecurityProcessor] = None  # type: ignore
    _inline_reference_cache: dict[str, Any] = field(default_factory=dict)
    # Inline references cache can be populated from multiple threads, therefore we need some synchronisation to avoid
    # excessive resolving
    _inline_reference_cache_lock: RLock = field(default_factory=RLock)
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = ()

    def __post_init__(self) -> None:
        self._spec = OpenApi(self.raw_schema)

    def __getitem__(self, path: str):
        return self._spec[path]

    @property
    def specification(self) -> Specification:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} for {self._spec.title} {self._spec.version}>"

    def __iter__(self) -> Iterator[str]:
        return iter(self.raw_schema.get("paths", {}))

    def _get_operation_map(self, path: str) -> APIOperationMap:
        path_item = self.raw_schema.get("paths", {})[path]
        with in_scope(self.resolver, self.location or ""):
            scope, path_item = self._resolve_path_item(path_item)
        self.dispatch_hook("before_process_path", HookContext(), path, path_item)
        map = APIOperationMap(self, {})
        map._data = MethodMap(map, scope, path, CaseInsensitiveDict(path_item))
        return map

    def find_operation_by_label(self, label: str) -> APIOperation | None:
        method, path = label.split(" ", maxsplit=1)
        return OpenApi(self.raw_schema)[path][method]

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        matches = get_close_matches(item, list(self))
        self._on_missing_operation(item, exc, matches)

    def _on_missing_operation(self, item: str, exc: KeyError, matches: list[str]) -> NoReturn:
        message = f"`{item}` not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    def _should_skip(self, operation: ApiOperation) -> bool:
        if self.filter_set.is_empty():
            return False
        return not self.filter_set.match(SimpleNamespace(operation=operation))

    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()

        should_skip = self._should_skip

        # For operationId lookup
        selected_operations_by_id: set[str] = set()
        # Tuples of (method, path)
        selected_operations_by_path: set[tuple[str, str]] = set()
        collected_links: list[dict] = []

        for result in self._spec:
            statistic.operations.total += 1
            if not isinstance(result, Ok):
                continue
            operation = result.ok()

            is_selected = not should_skip(operation)
            if is_selected:
                statistic.operations.selected += 1
                # Store both identifiers
                operation_id = operation.id
                if operation_id is not None:
                    selected_operations_by_id.add(operation_id)
                selected_operations_by_path.add((operation.method, operation.path))

            for response in operation.responses.values():
                links = response.links
                statistic.links.total += len(links)
                if is_selected:
                    for link in links.values():
                        collected_links.append(link.definition)

        for link in collected_links:
            operation_id = link.get("operationId")
            if operation_id is not None:
                if operation_id in selected_operations_by_id:
                    statistic.links.selected += 1
            else:
                operation = self._spec.find_operation_by_ref(link["operationRef"])
                if operation is not None and (operation.method, operation.path) in selected_operations_by_path:
                    statistic.links.selected += 1

        return statistic

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        """Iterate over all operations defined in the API.

        Each yielded item is either `Ok` or `Err`, depending on the presence of errors during schema processing.

        There are two cases for the `Err` variant:

          1. The error happened while resolving a group of API operations. In Open API, you can put all operations for
             a specific path behind a reference, and if it is not resolvable, then all operations won't be available
             for testing. For example - a file with those definitions was removed. Here we know the path but don't
             know what operations are there.
          2. Errors while processing a known API operation.

        In both cases, Schemathesis lets the callee decide what to do with these variants. It allows it to test valid
        operations and show errors for invalid ones.
        """
        __tracebackhide__ = True
        # Optimization: local variables are faster than attribute access
        schema = OpenApi(self.raw_schema)
        base_url = self.get_base_url()
        for result in self._spec:
            if isinstance(result, Ok):
                raw_operation = result.ok()
                operation = APIOperation(
                    path=raw_operation.path,
                    method=raw_operation.method,
                    base_url=base_url,
                    app=self.app,
                    schema=self,
                    inner=raw_operation,
                )
                # config = self.config.generation_for(operation=operation)
                # if config.with_security_parameters:
                #     self.security.process_definitions(self.raw_schema, operation, self.resolver)
                # self.dispatch_hook("before_init_operation", HookContext(operation=operation), operation)
                yield Ok(operation)
            else:
                yield result

    def validate(self) -> None:
        with suppress(TypeError):
            self._validate()

    def _validate(self) -> None:
        raise NotImplementedError

    @property
    def resolver(self) -> InliningResolver:
        if not hasattr(self, "_resolver"):
            self._resolver = InliningResolver(self.location or "", self.raw_schema)
        return self._resolver

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        raise NotImplementedError

    def get_security_requirements(self, operation: APIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        return self.security.get_security_requirements(self.raw_schema, operation)

    def get_operation_by_id(self, operation_id: str) -> APIOperation:
        """Get an `APIOperation` instance by its `operationId`."""
        resolve = self.resolver.resolve
        default_scope = self.resolver.resolution_scope
        for path, path_item in self.raw_schema.get("paths", {}).items():
            # If the path is behind a reference we have to keep the scope
            # The scope is used to resolve nested components later on
            if "$ref" in path_item:
                scope, path_item = resolve(path_item["$ref"])
            else:
                scope = default_scope
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if "operationId" in operation and operation["operationId"] == operation_id:
                    resolved = self._resolve_operation(operation)
                    parameters = self._collect_operation_parameters(path_item, resolved)
                    return self.make_operation(path, method, parameters, operation, resolved, scope)
        self._on_missing_operation(operation_id, None, [])

    def get_operation_by_reference(self, reference: str) -> APIOperation:
        """Get local or external `APIOperation` instance by reference.

        Reference example: #/paths/~1users~1{user_id}/patch
        """
        scope, operation = self.resolver.resolve(reference)
        path, method = scope.rsplit("/", maxsplit=2)[-2:]
        path = path.replace("~1", "/").replace("~0", "~")
        with in_scope(self.resolver, scope):
            resolved = self._resolve_operation(operation)
        parent_ref, _ = reference.rsplit("/", maxsplit=1)
        _, path_item = self.resolver.resolve(parent_ref)
        parameters = self._collect_operation_parameters(path_item, resolved)
        return self.make_operation(path, method, parameters, operation, resolved, scope)

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        return openapi_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            **kwargs,
        )

    def get_parameter_serializer(self, operation: APIOperation, location: str) -> Callable | None:
        definitions = [item.definition for item in operation.iter_parameters() if item.location == location]
        config = self.config.generation_for(operation=operation)
        if config.with_security_parameters:
            security_parameters = self.security.get_security_definitions_as_parameters(
                self.raw_schema, operation, self.resolver, location
            )
            security_parameters = [item for item in security_parameters if item["in"] == location]
            if security_parameters:
                definitions.extend(security_parameters)
        if definitions:
            return self._get_parameter_serializer(definitions)
        return None

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        raise NotImplementedError

    def get_headers(self, operation: APIOperation, response: Response) -> dict[str, Any] | None:
        resolved = operation.inner.get_response_definition(response.status_code)
        if not resolved:
            return None
        return resolved.headers

    def as_state_machine(self) -> type[APIStateMachine]:
        return create_state_machine(self)

    @property
    def validator_cls(self) -> type[jsonschema.Validator]:
        if self.specification.version.startswith("3.1"):
            return jsonschema.Draft202012Validator
        return jsonschema.Draft4Validator

    def validate_response(self, operation: APIOperation, response: Response) -> bool | None:
        __tracebackhide__ = True
        # TODO: Support wildcards
        responses = operation.responses
        definition = responses.get(str(response.status_code)) or responses.get("default")
        if definition is None:
            # No response defined for the received response status code
            return None

        schema = definition.schema
        if not schema:
            # No schema to check against
            return None
        content_types = response.headers.get("content-type")
        failures: list[Failure] = []
        if content_types is None:
            all_media_types = operation.inner.output_content_types_for(response.status_code)
            formatted_content_types = [f"\n- `{content_type}`" for content_type in all_media_types]
            message = f"The following media types are documented in the schema:{''.join(formatted_content_types)}"
            failures.append(MissingContentType(operation=operation.label, message=message, media_types=all_media_types))
            # Default content type
            content_type = "application/json"
        else:
            content_type = content_types[0]
        try:
            data = deserialization.deserialize_response(response, content_type)
        except JSONDecodeError as exc:
            failures.append(MalformedJson.from_exception(operation=operation.label, exc=exc))
            _maybe_raise_one_or_more(failures)
            return None
        except NotImplementedError:
            # If the content type is not supported, we cannot validate it
            _maybe_raise_one_or_more(failures)
            return None
        except Exception as exc:
            failures.append(
                Failure(
                    operation=operation.label,
                    title="Content deserialization error",
                    message=f"Failed to deserialize response content:\n\n  {exc}",
                )
            )
            _maybe_raise_one_or_more(failures)
            return None
        try:
            jsonschema.validate(
                data,
                schema,
                cls=self.validator_cls,
                _resolver=definition.resolver,
                # Use a recent JSON Schema format checker to get most of formats checked for older drafts as well
                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
            )
        except jsonschema.SchemaError as exc:
            raise InvalidSchema.from_jsonschema_error(
                exc, path=operation.path, method=operation.method, config=self.config.output
            ) from exc
        except jsonschema.ValidationError as exc:
            failures.append(
                JsonSchemaError.from_exception(
                    operation=operation.label,
                    exc=exc,
                    config=operation.schema.config.output,
                )
            )
        _maybe_raise_one_or_more(failures)
        return None  # explicitly return None for mypy

    @contextmanager
    def _validating_response(self, scopes: list[str]) -> Generator[ConvertingResolver, None, None]:
        resolver = ConvertingResolver(
            self.location or "", self.raw_schema, nullable_name=self.nullable_name, is_response_schema=True
        )
        with in_scopes(resolver, scopes):
            yield resolver

    @property
    def rewritten_components(self) -> dict[str, Any]:
        if not hasattr(self, "_rewritten_components"):

            def callback(_schema: dict[str, Any], nullable_name: str) -> dict[str, Any]:
                _schema = to_json_schema(_schema, nullable_name=nullable_name, copy=False)
                return self._rewrite_references(_schema, self.resolver)

            # Different spec versions allow different keywords to store possible reference targets
            components: dict[str, Any] = {}
            for path in self.component_locations:
                schema = self.raw_schema
                target = components
                for chunk in path:
                    if chunk in schema:
                        schema = schema[chunk]
                        target = target.setdefault(chunk, {})
                    else:
                        break
                else:
                    target.update(transform(deepclone(schema), callback, self.nullable_name))
            if self._inline_reference_cache:
                components[INLINED_REFERENCES_KEY] = self._inline_reference_cache
            self._rewritten_components = components
        return self._rewritten_components

    def prepare_schema(self, schema: Any) -> Any:
        """Inline Open API definitions.

        Inlining components helps `hypothesis-jsonschema` generate data that involves non-resolved references.
        """
        schema = deepclone(schema)
        schema = transform(schema, self._rewrite_references, self.resolver)
        # Only add definitions that are reachable from the schema via references
        stack = [schema]
        seen = set()
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "$ref" in item:
                    reference = item["$ref"]
                    if isinstance(reference, str) and reference.startswith("#/") and reference not in seen:
                        seen.add(reference)
                        # Resolve the component and add it to the proper place in the schema
                        pointer = reference[1:]
                        resolved = resolve_pointer(self.rewritten_components, pointer)
                        if resolved is UNRESOLVABLE:
                            raise LoaderError(
                                LoaderErrorKind.OPEN_API_INVALID_SCHEMA,
                                message=f"Unresolvable JSON pointer in the schema: {pointer}",
                            )
                        if isinstance(resolved, dict):
                            container = schema
                            for key in pointer.split("/")[1:]:
                                container = container.setdefault(key, {})
                            container.update(resolved)
                        # Explore the resolved value too
                        stack.append(resolved)
                # Still explore other values as they may have nested references in other keys
                for value in item.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                for sub_item in item:
                    if isinstance(sub_item, (dict, list)):
                        stack.append(sub_item)
        return schema

    def _rewrite_references(self, schema: dict[str, Any], resolver: InliningResolver) -> dict[str, Any]:
        """Rewrite references present in the schema.

        The idea is to resolve references, cache the result and replace these references with new ones
        that point to a local path which is populated from this cache later on.
        """
        reference = schema.get("$ref")
        # If `$ref` is not a property name and should be processed
        if reference is not None and isinstance(reference, str) and not reference.startswith("#/"):
            key = _make_reference_key(resolver._scopes_stack, reference)
            with self._inline_reference_cache_lock:
                if key not in self._inline_reference_cache:
                    with resolver.resolving(reference) as resolved:
                        # Resolved object also may have references
                        self._inline_reference_cache[key] = transform(
                            resolved, lambda s: self._rewrite_references(s, resolver)
                        )
            # Rewrite the reference with the new location
            schema["$ref"] = f"#/{INLINED_REFERENCES_KEY}/{key}"
        return schema


def _get_response_definition_by_status(status_code: int, responses: dict[str, Any]) -> dict[str, Any] | None:
    # Cast to string, as integers are often there due to YAML deserialization
    responses = {str(status): definition for status, definition in responses.items()}
    if str(status_code) in responses:
        return responses[str(status_code)]
    # More specific should go first
    keys = sorted(responses, key=lambda k: k.count("X"))
    for key in keys:
        if key == "default":
            continue
        status_codes = expand_status_code(key)
        if status_code in status_codes:
            return responses[key]
    if "default" in responses:
        return responses["default"]
    return None


def _maybe_raise_one_or_more(failures: list[Failure]) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0] from None
    raise FailureGroup(failures) from None


def _make_reference_key(scopes: list[str], reference: str) -> str:
    """A name under which the resolved reference data will be stored."""
    # Using a hexdigest is the simplest way to associate practically unique keys with each reference
    digest = sha1()
    for scope in scopes:
        digest.update(scope.encode("utf-8"))
        # Separator to avoid collisions like this: ["a"], "bc" vs. ["ab"], "c". Otherwise, the resulting digest
        # will be the same for both cases
        digest.update(b"#")
    digest.update(reference.encode("utf-8"))
    return digest.hexdigest()


INLINED_REFERENCES_KEY = "x-inlined"


@contextmanager
def in_scope(resolver: jsonschema.RefResolver, scope: str) -> Generator[None, None, None]:
    resolver.push_scope(scope)
    try:
        yield
    finally:
        resolver.pop_scope()


@contextmanager
def in_scopes(resolver: jsonschema.RefResolver, scopes: list[str]) -> Generator[None, None, None]:
    """Push all available scopes into the resolver.

    There could be an additional scope change during a schema resolving in `get_response_schema`, so in total there
    could be a stack of two scopes maximum. This context manager handles both cases (1 or 2 scope changes) in the same
    way.
    """
    with ExitStack() as stack:
        for scope in scopes:
            stack.enter_context(in_scope(resolver, scope))
        yield


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


class SwaggerV20(BaseOpenAPISchema):
    nullable_name = "x-nullable"
    example_field = "x-example"
    examples_field = "x-examples"
    header_required_field = "x-required"
    security = SwaggerSecurityProcessor()
    component_locations: ClassVar[tuple[tuple[str, ...], ...]] = (("definitions",),)
    links_field = "x-links"

    @property
    def specification(self) -> Specification:
        return Specification.openapi(version="2.0")

    def _validate(self) -> None:
        SWAGGER_20_VALIDATOR.validate(self.raw_schema)

    def _get_base_path(self) -> str:
        return self.raw_schema.get("basePath", "/")

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_swagger2_parameters(definitions)

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        files, data = [], {}
        # If there is no content types specified for the request or "application/x-www-form-urlencoded" is specified
        # explicitly, then use it., but if "multipart/form-data" is specified, then use it
        is_multipart = "multipart/form-data" in operation.input_content_types

        known_fields: dict[str, dict] = {}

        for parameter in operation.body:
            if isinstance(parameter, OpenAPI20CompositeBody):
                for form_parameter in parameter.definition:
                    known_fields[form_parameter.name] = form_parameter.definition

        def add_file(name: str, value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    files.append((name, (None, item)))
            else:
                files.append((name, value))

        for name, value in form_data.items():
            param_def = known_fields.get(name)
            if param_def:
                if param_def.get("type") == "file" or is_multipart:
                    add_file(name, value)
                else:
                    data[name] = value
            else:
                # Unknown field — treat it as a file (safe default under multipart/form-data)
                add_file(name, value)
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, data or None

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: str | None = None,
        path: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        if body is not NOT_SET and media_type is None:
            media_type = operation._get_default_media_type()
        return Case(
            operation=operation,
            method=method or operation.method.upper(),
            path=path or operation.path,
            path_parameters=path_parameters or {},
            headers=CaseInsensitiveDict() if headers is None else CaseInsensitiveDict(headers),
            cookies=cookies or {},
            query=query or {},
            body=body,
            media_type=media_type,
            meta=meta,
        )

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        for parameter in definition.get("parameters", []):
            if "$ref" in parameter:
                _, parameter = self.resolver.resolve(parameter["$ref"])
            if parameter["in"] == "body":
                return parameter["schema"]
        return None


class OpenApi30(SwaggerV20):
    nullable_name = "nullable"
    example_field = "example"
    examples_field = "examples"
    header_required_field = "required"
    security = OpenAPISecurityProcessor()
    component_locations = (("components", "schemas"),)
    links_field = "links"

    @property
    def specification(self) -> Specification:
        version = self.raw_schema["openapi"]
        return Specification.openapi(version=version)

    def _validate(self) -> None:
        if self.specification.version.startswith("3.1"):
            # Currently we treat Open API 3.1 as 3.0 in some regard
            OPENAPI_31_VALIDATOR.validate(self.raw_schema)
        else:
            OPENAPI_30_VALIDATOR.validate(self.raw_schema)

    def _get_base_path(self) -> str:
        servers = self.raw_schema.get("servers", [])
        if servers:
            # assume we're the first server in list
            server = servers[0]
            url = server["url"].format(**{k: v["default"] for k, v in server.get("variables", {}).items()})
            return urlsplit(url).path
        return "/"

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        """Get examples from the API operation."""
        return get_strategies_from_examples(operation, **kwargs)

    def _get_parameter_serializer(self, definitions: list[dict[str, Any]]) -> Callable | None:
        return serialization.serialize_openapi3_parameters(definitions)

    def prepare_multipart(
        self, form_data: dict[str, Any], operation: APIOperation
    ) -> tuple[list | None, dict[str, Any] | None]:
        files = []
        definition = operation.definition.raw
        if "$ref" in definition["requestBody"]:
            self.resolver.push_scope(operation.definition.scope)
            try:
                body = self.resolver.resolve_all(definition["requestBody"], RECURSION_DEPTH_LIMIT)
            finally:
                self.resolver.pop_scope()
        else:
            body = definition["requestBody"]
        content = body["content"]
        # Open API 3.0 requires media types to be present. We can get here only if the schema defines
        # the "multipart/form-data" media type, or any other more general media type that matches it (like `*/*`)
        for media_type, entry in content.items():
            main, sub = media_types.parse(media_type)
            if main in ("*", "multipart") and sub in ("*", "form-data", "mixed"):
                schema = entry.get("schema")
                break
        else:
            raise InternalError("No 'multipart/form-data' media type found in the schema")
        for name, value in form_data.items():
            property_schema = (schema or {}).get("properties", {}).get(name)
            if property_schema:
                if isinstance(value, list):
                    files.extend([(name, item) for item in value])
                elif property_schema.get("format") in ("binary", "base64"):
                    files.append((name, value))
                else:
                    files.append((name, (None, value)))
            elif isinstance(value, list):
                files.extend([(name, item) for item in value])
            else:
                files.append((name, (None, value)))
        # `None` is the default value for `files` and `data` arguments in `requests.request`
        return files or None, None

    def _get_payload_schema(self, definition: dict[str, Any], media_type: str) -> dict[str, Any] | None:
        if "requestBody" in definition:
            if "$ref" in definition["requestBody"]:
                body = self.resolver.resolve_all(definition["requestBody"], RECURSION_DEPTH_LIMIT)
            else:
                body = definition["requestBody"]
            if "content" in body:
                main, sub = media_types.parse(media_type)
                for defined_media_type, item in body["content"].items():
                    if media_types.parse(defined_media_type) == (main, sub):
                        return item["schema"]
        return None
