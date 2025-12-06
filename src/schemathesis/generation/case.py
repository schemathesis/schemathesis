from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis import transport
from schemathesis.checks import CHECKS, CheckContext, CheckFunction, load_all_checks, run_checks
from schemathesis.core import NOT_SET, SCHEMATHESIS_TEST_CASE_HEADER, NotSet, curl
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.failures import FailureGroup, failure_report_title, format_failures
from schemathesis.core.parameters import CONTAINER_TO_LOCATION, ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode, generate_random_case_id
from schemathesis.generation.meta import CaseMetadata, ComponentInfo
from schemathesis.generation.overrides import Override, store_components
from schemathesis.hooks import HookContext, dispatch
from schemathesis.transport.prepare import prepare_path, prepare_request

if TYPE_CHECKING:
    import httpx
    import requests
    import requests.auth
    from requests.structures import CaseInsensitiveDict
    from werkzeug.test import TestResponse

    from schemathesis.schemas import APIOperation


def _default_headers() -> CaseInsensitiveDict:
    from requests.structures import CaseInsensitiveDict

    return CaseInsensitiveDict()


_NOTSET_HASH = 0x7F3A9B2C


@dataclass
class Case:
    """Generated test case data for a single API operation."""

    operation: APIOperation
    method: str
    """HTTP verb (`GET`, `POST`, etc.)"""
    path: str
    """Path template from schema (e.g., `/users/{user_id}`)"""
    id: str
    """Random ID sent in headers for log correlation"""
    path_parameters: dict[str, Any]
    """Generated path variables (e.g., `{"user_id": "123"}`)"""
    headers: CaseInsensitiveDict
    """Generated HTTP headers"""
    cookies: dict[str, Any]
    """Generated cookies"""
    query: dict[str, Any]
    """Generated query parameters"""
    # By default, there is no body, but we can't use `None` as the default value because it clashes with `null`
    # which is a valid payload.
    body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet
    """Generated request body"""
    media_type: str | None
    """Media type from OpenAPI schema (e.g., "multipart/form-data")"""
    multipart_content_types: dict[str, str] | None
    """Selected content types for multipart form properties (e.g., {"image": "image/png"})"""

    _meta: CaseMetadata | None

    _auth: requests.auth.AuthBase | None
    _has_explicit_auth: bool
    _components: dict
    _freeze_metadata: bool

    __slots__ = (
        "operation",
        "method",
        "path",
        "id",
        "path_parameters",
        "headers",
        "cookies",
        "query",
        "body",
        "media_type",
        "multipart_content_types",
        "_meta",
        "_auth",
        "_has_explicit_auth",
        "_components",
        "_freeze_metadata",
    )

    def __init__(
        self,
        operation: APIOperation,
        method: str,
        path: str,
        *,
        id: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        multipart_content_types: dict[str, str] | None = None,
        meta: CaseMetadata | None = None,
        _auth: requests.auth.AuthBase | None = None,
        _has_explicit_auth: bool = False,
    ) -> None:
        # Use object.__setattr__ to bypass __setattr__ tracking during initialization
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "id", id if id is not None else generate_random_case_id())
        object.__setattr__(self, "path_parameters", path_parameters if path_parameters is not None else {})
        object.__setattr__(self, "headers", headers if headers is not None else _default_headers())
        object.__setattr__(self, "cookies", cookies if cookies is not None else {})
        object.__setattr__(self, "query", query if query is not None else {})
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "multipart_content_types", multipart_content_types)
        object.__setattr__(self, "_meta", meta)
        object.__setattr__(self, "_auth", _auth)
        object.__setattr__(self, "_has_explicit_auth", _has_explicit_auth)
        object.__setattr__(self, "_components", store_components(self))
        object.__setattr__(self, "_freeze_metadata", False)

        # Initialize hash tracking if we have metadata
        if self._meta is not None:
            self._init_hashes()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Case):
            return NotImplemented

        return (
            self.operation == other.operation
            and self.method == other.method
            and self.path == other.path
            and self.path_parameters == other.path_parameters
            and self.headers == other.headers
            and self.cookies == other.cookies
            and self.query == other.query
            and self.body == other.body
            and self.media_type == other.media_type
        )

    def __setattr__(self, name: str, value: Any) -> None:
        """Track modifications to containers for metadata revalidation."""
        # Set the value
        object.__setattr__(self, name, value)

        # Mark as dirty if we modified a tracked container and have metadata
        if name in CONTAINER_TO_LOCATION and self._meta is not None:
            location = CONTAINER_TO_LOCATION[name]
            self._meta.mark_dirty(location)
            # Update hash immediately so future in-place modifications can be detected
            self._meta.update_validated_hash(location, self._hash_container(value))

    @property
    def _override(self) -> Override:
        return Override.from_components(self._components, self)

    def __repr__(self) -> str:
        output = f"{self.__class__.__name__}("
        first = True
        for name in ("path_parameters", "headers", "cookies", "query", "body"):
            value = getattr(self, name)
            if name != "body" and not value:
                continue
            if value is not None and not isinstance(value, NotSet):
                if first:
                    first = False
                else:
                    output += ", "
                output += f"{name}={value!r}"
        return f"{output})"

    def __hash__(self) -> int:
        return hash(self.as_curl_command({SCHEMATHESIS_TEST_CASE_HEADER: "0"}))

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def _init_hashes(self) -> None:
        """Initialize hash tracking in metadata for generated components only."""
        assert self._meta is not None
        # Only track components that were actually generated
        for location in self._meta.components.keys():
            value = getattr(self, location.container_name)
            hash_value = self._hash_container(value)
            self._meta.update_validated_hash(location, hash_value)

    def _check_modifications(self) -> None:
        """Detect in-place modifications by comparing container hashes."""
        if self._meta is None:
            return

        # Only check components that were actually generated
        for location in self._meta.components.keys():
            last_hash = self._meta._last_validated_hashes[location]
            value = getattr(self, location.container_name)
            current_hash = self._hash_container(value)

            if current_hash != last_hash:
                # Container was modified in-place
                self._meta.mark_dirty(location)

    def _revalidate_metadata(self) -> None:
        """Revalidate dirty components and update metadata."""
        assert self._meta and self._meta.is_dirty()

        from schemathesis.specs.openapi.schemas import OpenApiSchema

        # Only works for OpenAPI schemas
        if not isinstance(self.operation.schema, OpenApiSchema):
            # Can't validate, just clear dirty flags
            for location in list(self._meta._dirty):
                self._meta.clear_dirty(location)
            return

        validator_cls = self.operation.schema.adapter.jsonschema_validator_cls

        for location in list(self._meta._dirty):
            # Get current value
            value = getattr(self, location.container_name)

            # Validate against schema
            is_valid = self._validate_component(location, value, validator_cls)

            # Update component metadata
            if location in self._meta.components:
                new_mode = GenerationMode.POSITIVE if is_valid else GenerationMode.NEGATIVE
                self._meta.components[location] = ComponentInfo(mode=new_mode)

            # Update hash and clear dirty flag
            self._meta.update_validated_hash(location, self._hash_container(value))
            self._meta.clear_dirty(location)

        # Recompute overall generation mode
        if self._meta.components:
            if all(info.mode.is_positive for info in self._meta.components.values()):
                self._meta.generation.mode = GenerationMode.POSITIVE
            else:
                self._meta.generation.mode = GenerationMode.NEGATIVE

    def _validate_component(
        self,
        location: ParameterLocation,
        value: Any,
        validator_cls: type,
    ) -> bool:
        """Validate a component value against its schema."""
        if location == ParameterLocation.BODY:
            # Validate body against media type schema
            if isinstance(value, NotSet) or value is None:
                return False
            for alternative in self.operation.body:
                if alternative.media_type == self.media_type:
                    return validator_cls(alternative.optimized_schema).is_valid(value)
        # Validate other locations against container schema
        container = getattr(self.operation, location.container_name)
        return validator_cls(container.schema).is_valid(value)

    def _hash_container(self, value: Any) -> int:
        """Create a hash representing the current state of a container.

        Recursively hashes nested dicts/lists/tuples and primitives to detect modifications.
        """
        if isinstance(value, Mapping):
            return hash((type(value), tuple(sorted((k, self._hash_container(v)) for k, v in value.items()))))
        elif isinstance(value, (list, tuple)):
            return hash((type(value), tuple(self._hash_container(item) for item in value)))
        elif isinstance(value, NotSet):
            return _NOTSET_HASH
        return hash((type(value), value))

    @property
    def meta(self) -> CaseMetadata | None:
        """Get metadata, revalidating if components were modified."""
        # Skip revalidation if metadata is frozen (e.g., during request preparation)
        if not self._freeze_metadata:
            self._check_modifications()
            if self._meta and self._meta.is_dirty():
                self._revalidate_metadata()
        return self._meta

    @property
    def formatted_path(self) -> str:
        """Path template with variables substituted (e.g., /users/{user_id} → /users/123)."""
        return prepare_path(self.path, self.path_parameters)

    def as_curl_command(self, headers: Mapping[str, Any] | None = None, verify: bool = True) -> str:
        """Generate a curl command that reproduces this test case.

        Args:
            headers: Additional headers to include in the command.
            verify: When False, adds `--insecure` flag to curl command.

        """
        request_data = prepare_request(self, headers, config=self.operation.schema.config.output.sanitization)
        result = curl.generate(
            method=str(request_data.method),
            url=str(request_data.url),
            body=request_data.body,
            verify=verify,
            headers=dict(request_data.headers),
            known_generated_headers=dict(self.headers or {}),
        )
        # Include warnings if any exist
        if result.warnings:
            warnings_text = "\n\n".join(f"⚠️  {warning}" for warning in result.warnings)
            return f"{result.command}\n\n{warnings_text}"
        return result.command

    def as_transport_kwargs(self, base_url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self.operation.schema.transport.serialize_case(self, base_url=base_url, headers=headers)

    def call(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Make an HTTP request using this test case's data without validation.

        Use when you need to validate response separately

        Args:
            base_url: Override the schema's base URL.
            session: Reuse an existing requests session.
            headers: Additional headers.
            params: Additional query parameters.
            cookies: Additional cookies.
            **kwargs: Additional transport-level arguments.

        """
        hook_context = HookContext(operation=self.operation)
        dispatch("before_call", hook_context, self, _with_dual_style_kwargs=True, **kwargs)

        # Revalidate metadata if dirty before freezing (captures user modifications)
        if self._meta and self._meta.is_dirty():
            self._check_modifications()
            self._revalidate_metadata()

        # Freeze metadata to prevent revalidation after request preparation transforms the body
        object.__setattr__(self, "_freeze_metadata", True)

        if self.operation.app is not None:
            kwargs.setdefault("app", self.operation.app)
        if "app" in kwargs:
            transport_ = transport.get(kwargs["app"])
        else:
            transport_ = self.operation.schema.transport
        try:
            response = transport_.send(
                self,
                session=session,
                base_url=base_url,
                headers=headers,
                params=params,
                cookies=cookies,
                **kwargs,
            )
        except IncorrectUsage:
            # Configuration errors - don't add reproduction code
            raise
        except Exception as exc:
            # Add reproduction code for check failures and app errors (e.g., ASGI/WSGI internal errors)
            if not hasattr(exc, "__notes__"):
                exc.__notes__ = []  # type: ignore[attr-defined]
            verify = kwargs.get("verify", True)
            try:
                curl = self.as_curl_command(headers=headers, verify=verify)
                exc.__notes__.append(f"\nReproduce with: \n\n    {curl}")  # type: ignore[attr-defined]
            except Exception:
                # Curl generation can fail for the same reason as the original error
                # (e.g., malformed path template). Skip adding curl command to avoid
                # replacing the original exception with a secondary error.
                pass
            raise
        dispatch("after_call", hook_context, self, response)
        return response

    def validate_response(
        self,
        response: Response | httpx.Response | requests.Response | TestResponse,
        checks: list[CheckFunction] | None = None,
        additional_checks: list[CheckFunction] | None = None,
        excluded_checks: list[CheckFunction] | None = None,
        headers: dict[str, Any] | None = None,
        transport_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Validate a response against the API schema and built-in checks.

        Args:
            response: Response to validate.
            checks: Explicit set of checks to run.
            additional_checks: Additional custom checks to run.
            excluded_checks: Built-in checks to skip.
            headers: Headers used in the original request.
            transport_kwargs: Transport arguments used in the original request.

        """
        __tracebackhide__ = True
        from requests.structures import CaseInsensitiveDict

        # In some cases checks may not be loaded.
        # For example - non-Schemathesis tests that manually construct `Case` instances
        load_all_checks()

        response = Response.from_any(response)

        config = self.operation.schema.config.checks_config_for(
            operation=self.operation, phase=self.meta.phase.name.value if self.meta is not None else None
        )
        if not checks:
            # Checks are not specified explicitly, derive from the config
            checks = []
            for check in CHECKS.get_all():
                name = check.__name__
                if config.get_by_name(name=name).enabled:
                    checks.append(check)
        checks = [
            check for check in list(checks) + list(additional_checks or []) if check not in set(excluded_checks or [])
        ]

        ctx = CheckContext(
            override=self._override,
            auth=transport_kwargs.get("auth") if transport_kwargs else None,
            headers=CaseInsensitiveDict(headers) if headers else None,
            config=config,
            transport_kwargs=transport_kwargs,
            recorder=None,
        )
        failures = run_checks(
            case=self,
            response=response,
            ctx=ctx,
            checks=checks,
            on_failure=lambda _, collected, failure: collected.add(failure),
        )
        if failures:
            _failures = list(failures)
            message = failure_report_title(_failures) + "\n"
            verify = getattr(response, "verify", True)
            curl = self.as_curl_command(headers=dict(response.request.headers), verify=verify)
            message += format_failures(
                case_id=None,
                response=response,
                failures=_failures,
                curl=curl,
                config=self.operation.schema.config.output,
            )
            message += "\n\n"
            raise FailureGroup(_failures, message) from None

    def call_and_validate(
        self,
        base_url: str | None = None,
        session: requests.Session | None = None,
        headers: dict[str, Any] | None = None,
        checks: list[CheckFunction] | None = None,
        additional_checks: list[CheckFunction] | None = None,
        excluded_checks: list[CheckFunction] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Make an HTTP request and validates the response automatically.

        Args:
            base_url: Override the schema's base URL.
            session: Reuse an existing requests session.
            headers: Additional headers to send.
            checks: Explicit set of checks to run.
            additional_checks: Additional custom checks to run.
            excluded_checks: Built-in checks to skip.
            **kwargs: Additional transport-level arguments.

        """
        __tracebackhide__ = True
        call_kwargs = dict(kwargs)
        response = self.call(base_url, session, headers, **call_kwargs)
        transport_kwargs = dict(kwargs)
        if base_url is not None:
            transport_kwargs["base_url"] = base_url
        if session is not None:
            transport_kwargs.setdefault("session", session)
        if headers is not None:
            transport_kwargs.setdefault("headers", dict(headers))
        self.validate_response(
            response,
            checks,
            headers=headers,
            additional_checks=additional_checks,
            excluded_checks=excluded_checks,
            transport_kwargs=transport_kwargs,
        )
        return response
