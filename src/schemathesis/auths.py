from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Protocol,
    Self,
    TypeVar,
    cast,
    overload,
    runtime_checkable,
)

from schemathesis.core.errors import AuthenticationError, IncorrectUsage, SchemathesisError
from schemathesis.core.marks import Mark
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import RequestSettings, StatusClass, decode_lossy, load_json_lossy, parse_set_cookie
from schemathesis.filters import FilterSet, FilterValue, MatcherFunc, RegexValue, attach_filter_chain
from schemathesis.generation.meta import negated_parameters
from schemathesis.transport import is_asgi_app

if TYPE_CHECKING:
    import requests.auth
    from werkzeug.datastructures import Headers

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.python.asgi import ASGIApp
    from schemathesis.schemas import APIOperation, BaseSchema

DEFAULT_REFRESH_INTERVAL = 300
# Consecutive failed re-logins before the breaker trips and disables reauth for the run.
REAUTH_BREAKER_THRESHOLD = 3
# Consecutive failed token fetches before a caching provider stops hammering the login endpoint.
TOKEN_FETCH_BREAKER_THRESHOLD = 3
# Seconds a tripped token-fetch breaker waits before letting a single probe through.
TOKEN_FETCH_BREAKER_COOLDOWN = 30.0
AuthStorageMark = Mark["AuthStorage"](attr_name="auth_storage")
Auth = TypeVar("Auth")


@dataclass(slots=True)
class AuthContext:
    """Runtime context passed to authentication providers during token generation.

    Gives providers the current API operation and application instance, for operation-specific tokens or app state.

    Example:
        ```python
        @schemathesis.auth()
        class ContextAwareAuth:
            def get(self, case, context):
                # Access operation details
                if "/admin/" in context.operation.path:
                    return self.get_admin_token()
                else:
                    return self.get_user_token()

            def set(self, case, data, context):
                case.headers = {"Authorization": f"Bearer {data}"}
        ```

    """

    operation: APIOperation
    """API operation currently being processed for authentication."""
    app: Any | None
    """Python application instance (ASGI/WSGI app) when using app integration, `None` otherwise."""
    # The API rejected the cached credentials; a failed re-login must fail, not replay the request with them.
    _reauth: bool = False


@dataclass(slots=True)
class AuthEndpointResponse:
    """Response from an endpoint that hands out credentials."""

    status_code: int
    text: str
    get_json: Callable[[], object]
    headers: Mapping[str, str] | Headers
    cookies: dict[str, str]


class InProcessAuthRequestError(Exception):
    """An in-process app failed to serve a request for credentials."""

    def __init__(self, interface: Literal["ASGI", "WSGI"], cause: Exception) -> None:
        super().__init__(f"{interface} request failed: {cause}")
        self.interface = interface
        self.cause = cause


def send_auth_request(
    app: object | None,
    *,
    method: str,
    url: str,
    path: str,
    body: str | bytes | None,
    headers: dict[str, str],
    settings: RequestSettings,
    allow_redirects: bool,
) -> AuthEndpointResponse:
    """Request credentials from `url` over the network, or from `path` of the in-process `app`.

    Network failures propagate as `requests` raises them; in-process failures raise `InProcessAuthRequestError`.
    """
    if app is None:
        import requests

        response = requests.request(
            method,
            url,
            data=body,
            headers=headers,
            allow_redirects=allow_redirects,
            timeout=settings.network_timeout,
            verify=settings.verify,
            cert=settings.cert,
            proxies=settings.proxies,
        )
        return _from_requests_response(response)
    if is_asgi_app(app):
        return _send_asgi_auth_request(
            cast("ASGIApp", app),
            method=method,
            path=path,
            body=body,
            headers=headers,
            timeout=settings.timeout,
            allow_redirects=allow_redirects,
        )
    return _send_wsgi_auth_request(app, method=method, path=path, body=body, headers=headers)


def _send_asgi_auth_request(
    app: ASGIApp,
    *,
    method: str,
    path: str,
    body: str | bytes | None,
    headers: dict[str, str],
    timeout: float | int | None,
    allow_redirects: bool,
) -> AuthEndpointResponse:
    from schemathesis.python import asgi

    try:
        with asgi.get_client(app) as client:
            response = client.request(
                method, path, data=body, headers=headers, allow_redirects=allow_redirects, timeout=timeout
            )
    except Exception as exc:
        raise InProcessAuthRequestError("ASGI", exc) from exc
    return _from_requests_response(response)


def _send_wsgi_auth_request(
    app: object, *, method: str, path: str, body: str | bytes | None, headers: dict[str, str]
) -> AuthEndpointResponse:
    from schemathesis.python import wsgi

    try:
        client = wsgi.get_client(app)
        response = client.open(path, method=method, data=body, headers=headers)
        # Decoding stays inside: a body that is not UTF-8 is the app's failure, not a network one.
        text = response.get_data(as_text=True)
    except Exception as exc:
        raise InProcessAuthRequestError("WSGI", exc) from exc
    return AuthEndpointResponse(
        status_code=response.status_code,
        text=text,
        get_json=lambda: response.get_json(force=True, silent=False),
        headers=response.headers,
        cookies=parse_set_cookie(response.headers.getlist("Set-Cookie")),
    )


def _from_requests_response(response: requests.Response) -> AuthEndpointResponse:
    return AuthEndpointResponse(
        status_code=response.status_code,
        text=decode_lossy(response.content, response.encoding),
        get_json=lambda: load_json_lossy(response.content, response.encoding),
        headers=response.headers,
        cookies=response.cookies.get_dict(),
    )


CacheKeyFunction = Callable[["Case", "AuthContext"], str | int]


@runtime_checkable
class AuthProvider(Generic[Auth], Protocol):
    """Protocol for implementing custom authentication in API tests."""

    def get(self, case: Case, ctx: AuthContext, /) -> Auth | None:
        """Obtain authentication data for the test case.

        Args:
            case: Generated test case requiring authentication.
            ctx: Authentication state and configuration.

        Returns:
            Authentication data (e.g., token, credentials) or `None`.

        """
        ...  # pragma: no cover

    def set(self, case: Case, data: Auth, ctx: AuthContext, /) -> None:
        """Apply authentication data to the test case.

        Args:
            case: Test case to modify.
            data: Authentication data from the `get` method.
            ctx: Authentication state and configuration.

        """
        ...  # pragma: no cover


@dataclass
class CacheEntry(Generic[Auth]):
    data: Auth
    expires: float


@dataclass
class RequestsAuth(Generic[Auth]):
    """Provider that sets auth data via `requests` auth instance."""

    auth: requests.auth.AuthBase

    def get(self, _: Case, __: AuthContext) -> Auth | None:
        return self.auth  # type: ignore[return-value]

    def set(self, case: Case, _: Auth, __: AuthContext) -> None:
        case._auth = self.auth


@dataclass
class CachingAuthProvider(Generic[Auth]):
    provider: AuthProvider
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    cache_entry: CacheEntry[Auth] | None = None
    # Injectable to simplify testing
    timer: Callable[[], float] = time.monotonic
    # Statuses that invalidate the cache and refetch on the next request; `None` is the unauthenticated status.
    retry_on: list[int] | None = None
    _refresh_lock: threading.Lock = field(default_factory=threading.Lock)
    _fetch_failures: dict[str | int | None, int] = field(default_factory=dict)
    _fetch_disabled_until: dict[str | int | None, float] = field(default_factory=dict)
    # Error that tripped the breaker, shown while its cooldown lasts.
    _fetch_errors: dict[str | int | None, str] = field(default_factory=dict)

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        __tracebackhide__ = True
        try:
            return self._get_or_fetch(case, context)
        except AuthenticationError:
            last = self._get_cache_entry(case, context)
            # The API may still accept the last credentials, e.g. after a test changed the login password.
            if last is None or context._reauth:
                raise
            return last.data

    def _get_or_fetch(self, case: Case, context: AuthContext) -> Auth | None:
        __tracebackhide__ = True
        cache_entry = self._get_cache_entry(case, context)
        if cache_entry is None or self.timer() >= cache_entry.expires:
            with self._refresh_lock:
                cache_entry = self._get_cache_entry(case, context)
                if not (cache_entry is None or self.timer() >= cache_entry.expires):
                    # Another thread updated the cache
                    return cache_entry.data
                # A dead credential fails every fetch; stop hammering the login endpoint once tripped.
                key = self._fetch_key(case, context)
                disabled_until = self._fetch_disabled_until.get(key)
                if disabled_until is not None:
                    # The message stays constant during the cooldown, so replays report one failure, not one per second.
                    if self.timer() < disabled_until:
                        raise AuthenticationError(
                            self.provider.__class__.__name__,
                            "get",
                            f"Token fetch failed {TOKEN_FETCH_BREAKER_THRESHOLD} times in a row; "
                            f"retrying after a {TOKEN_FETCH_BREAKER_COOLDOWN:.0f}s cooldown\n\n"
                            f"Last error: {self._fetch_errors[key]}",
                        )
                    # The cooldown elapsed, so a transient outage gets one probe.
                    del self._fetch_disabled_until[key]
                # Optional auth exists only inside a higher-level wrapper
                try:
                    data: Auth = self.provider.get(case, context)  # type: ignore[assignment]
                except AuthenticationError as exc:
                    self._note_fetch_failure(key, exc.message)
                    raise
                except Exception as exc:
                    self._note_fetch_failure(key, str(exc))
                    provider_name = self.provider.__class__.__name__
                    raise AuthenticationError(
                        provider_name,
                        "get",
                        str(exc),
                        show_traceback=_has_useful_traceback(exc),
                        include_provider_context=True,
                    ) from exc
                self._fetch_failures.pop(key, None)
                self._set_cache_entry(data, case, context)
                return data
        return cache_entry.data

    def _fetch_key(self, case: Case, context: AuthContext) -> str | int | None:
        return None

    def _note_fetch_failure(self, key: str | int | None, message: str) -> None:
        count = self._fetch_failures.get(key, 0) + 1
        self._fetch_failures[key] = count
        if count >= TOKEN_FETCH_BREAKER_THRESHOLD:
            self._fetch_disabled_until[key] = self.timer() + TOKEN_FETCH_BREAKER_COOLDOWN
            self._fetch_errors[key] = message

    def _get_cache_entry(self, case: Case, context: AuthContext) -> CacheEntry[Auth] | None:
        return self.cache_entry

    def _set_cache_entry(self, data: Auth, case: Case, context: AuthContext) -> None:
        self.cache_entry = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        self.provider.set(case, data, context)

    def invalidate(self) -> None:
        # Expire without dropping, so a failed refetch can fall back to the last credentials.
        if self.cache_entry is not None:
            self.cache_entry.expires = -math.inf


def _noop_key_function(case: Case, context: AuthContext) -> str:
    # Never used
    raise NotImplementedError


@dataclass
class KeyedCachingAuthProvider(CachingAuthProvider[Auth]):
    cache_by_key: CacheKeyFunction = _noop_key_function
    cache_entries: dict[str | int, CacheEntry[Auth] | None] = field(default_factory=dict)

    def _get_cache_entry(self, case: Case, context: AuthContext) -> CacheEntry[Auth] | None:
        key = self.cache_by_key(case, context)
        return self.cache_entries.get(key)

    def _set_cache_entry(self, data: Auth, case: Case, context: AuthContext) -> None:
        key = self.cache_by_key(case, context)
        self.cache_entries[key] = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)

    def _fetch_key(self, case: Case, context: AuthContext) -> str | int | None:
        return self.cache_by_key(case, context)

    def invalidate(self) -> None:
        for entry in self.cache_entries.values():
            if entry is not None:
                entry.expires = -math.inf


class _FilterChain(Protocol):
    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> Self: ...  # pragma: no cover

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        tag: FilterValue | None = None,
        tag_regex: RegexValue | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
        operation_id: FilterValue | None = None,
        operation_id_regex: RegexValue | None = None,
    ) -> Self: ...  # pragma: no cover


class FilterableRegisterAuth(_FilterChain, Protocol):
    """Protocol that adds filters to the return value of `register`."""

    def __call__(self, provider_class: type[AuthProvider]) -> type[AuthProvider]: ...  # pragma: no cover


class FilterableApplyAuth(_FilterChain, Protocol):
    """Protocol that adds filters to the return value of `apply`."""

    def __call__(self, test: Callable) -> Callable: ...  # pragma: no cover


class FilterableRequestsAuth(_FilterChain, Protocol):
    """Protocol that adds filters to the return value of `set_from_requests`."""


def _has_useful_traceback(exc: Exception) -> bool:
    # Built-in providers raise Schemathesis errors whose message already names the cause.
    return not isinstance(exc, SchemathesisError)


@dataclass
class SelectiveAuthProvider(Generic[Auth]):
    """Applies auth depending on the configured filters."""

    provider: AuthProvider
    filter_set: FilterSet

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        __tracebackhide__ = True
        if self.filter_set.match(context):
            try:
                return self.provider.get(case, context)
            except AuthenticationError:
                raise
            except Exception as exc:
                # Unwrap caching layers to name the actual provider class
                provider = self.provider
                while isinstance(provider, CachingAuthProvider):
                    provider = provider.provider
                provider_name = provider.__class__.__name__
                raise AuthenticationError(
                    provider_name,
                    "get",
                    str(exc),
                    show_traceback=_has_useful_traceback(exc),
                    include_provider_context=True,
                ) from exc
        return None

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        __tracebackhide__ = True
        self.provider.set(case, data, context)


@dataclass
class AuthStorage(Generic[Auth]):
    providers: list[AuthProvider] = field(default_factory=list)

    @property
    def is_defined(self) -> bool:
        return bool(self.providers)

    @overload
    def __call__(
        self,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
    ) -> FilterableRegisterAuth: ...

    @overload
    def __call__(
        self,
        provider_class: type[AuthProvider],
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
    ) -> FilterableApplyAuth: ...

    def __call__(
        self,
        provider_class: type[AuthProvider] | None = None,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
    ) -> FilterableRegisterAuth | FilterableApplyAuth:
        if provider_class is not None:
            return self.apply(
                provider_class, refresh_interval=refresh_interval, cache_by_key=cache_by_key, retry_on=retry_on
            )
        return self.auth(refresh_interval=refresh_interval, cache_by_key=cache_by_key, retry_on=retry_on)

    def set_from_requests(self, auth: requests.auth.AuthBase) -> FilterableRequestsAuth:
        """Use `requests` auth instance as an auth provider."""
        filter_set = FilterSet()
        self.providers.append(SelectiveAuthProvider(provider=RequestsAuth(auth), filter_set=filter_set))

        class _FilterableRequestsAuth: ...

        attach_filter_chain(_FilterableRequestsAuth, "apply_to", filter_set.include)
        attach_filter_chain(_FilterableRequestsAuth, "skip_for", filter_set.exclude)

        return _FilterableRequestsAuth  # type: ignore[return-value]

    def _set_provider(
        self,
        *,
        provider_class: type[AuthProvider],
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
        filter_set: FilterSet,
    ) -> None:
        if not issubclass(provider_class, AuthProvider):
            raise TypeError(
                f"`{provider_class.__name__}` does not implement the `AuthProvider` protocol. "
                f"Auth providers must have `get` and `set` methods. "
                f"See `schemathesis.AuthProvider` documentation for examples."
            )
        provider: AuthProvider
        instance = provider_class()
        if refresh_interval is not None:
            if cache_by_key is None:
                provider = CachingAuthProvider(instance, refresh_interval=refresh_interval, retry_on=retry_on)
            else:
                provider = KeyedCachingAuthProvider(
                    instance,
                    refresh_interval=refresh_interval,
                    cache_by_key=cache_by_key,
                    retry_on=retry_on,
                )
        else:
            provider = instance
        if not filter_set.is_empty():
            provider = SelectiveAuthProvider(provider, filter_set)
        self.providers.append(provider)

    def auth(
        self,
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
    ) -> FilterableRegisterAuth:
        filter_set = FilterSet()

        def wrapper(provider_class: type[AuthProvider]) -> type[AuthProvider]:
            self._set_provider(
                provider_class=provider_class,
                refresh_interval=refresh_interval,
                filter_set=filter_set,
                cache_by_key=cache_by_key,
                retry_on=retry_on,
            )
            return provider_class

        attach_filter_chain(wrapper, "apply_to", filter_set.include)
        attach_filter_chain(wrapper, "skip_for", filter_set.exclude)

        return wrapper  # type: ignore[return-value]

    def unregister(self) -> None:
        self.providers = []

    def apply(
        self,
        provider_class: type[AuthProvider],
        *,
        refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
        cache_by_key: CacheKeyFunction | None = None,
        retry_on: list[int] | None = None,
    ) -> FilterableApplyAuth:
        filter_set = FilterSet()

        def wrapper(test: Callable) -> Callable:
            if AuthStorageMark.is_set(test):
                raise IncorrectUsage(f"`{test.__name__}` has already been decorated with `apply`.")
            auth_storage = self.__class__()
            AuthStorageMark.set(test, auth_storage)
            auth_storage._set_provider(
                provider_class=provider_class,
                refresh_interval=refresh_interval,
                filter_set=filter_set,
                cache_by_key=cache_by_key,
                retry_on=retry_on,
            )
            return test

        attach_filter_chain(wrapper, "apply_to", filter_set.include)
        attach_filter_chain(wrapper, "skip_for", filter_set.exclude)

        return wrapper  # type: ignore[return-value]

    def set(self, case: Case, context: AuthContext) -> None:
        """Set authentication data on a generated test case."""
        __tracebackhide__ = True
        if not self.is_defined:
            raise IncorrectUsage("No auth provider is defined.")
        for provider in self.providers:
            data: Auth | None = provider.get(case, context)
            if data is not None:
                provider.set(case, data, context)
                case._has_explicit_auth = True
                break


def apply_basic_auth(case: Case, username: str, password: str) -> None:
    from requests.auth import _basic_auth_str

    case.headers["Authorization"] = _basic_auth_str(username, password)
    case._has_explicit_auth = True


def _should_skip_auth_for_negative_testing(case: Case, param_name: str, param_location: ParameterLocation) -> bool:
    return (param_name, param_location) in negated_parameters(case.meta)


def set_on_generated_case(
    case: Case,
    context: AuthContext,
    auth_storage: AuthStorage | None,
    supplied: frozenset[tuple[ParameterLocation, str]] = frozenset(),
) -> None:
    """Set authentication data on a generated case, dropping credentials generated for unused security requirements."""
    __tracebackhide__ = True
    generated = []
    for location, name in case.operation.schema.alternative_credentials(case.operation):
        container = case.get_container(location)
        if (location, name) not in supplied and isinstance(container, MutableMapping) and name in container:
            generated.append((location, name, container[name]))
    set_on_case(case, context, auth_storage)
    # Configured auth satisfies one security requirement; random credentials for the others can only get it rejected.
    if case._has_explicit_auth:
        for location, name, value in generated:
            container = case.get_container(location)
            if isinstance(container, MutableMapping) and container.get(name) is value:
                del container[name]


def set_on_case(case: Case, context: AuthContext, auth_storage: AuthStorage | None) -> None:
    """Set authentication data on this case from the highest-priority auth source that is defined."""
    __tracebackhide__ = True
    # 1. Programmatic auth
    if auth_storage is not None:
        # Credentials re-applied later must come from the same source.
        case._auth_storage = auth_storage
        if not case.operation.schema.is_security_param_negated(case):
            auth_storage.set(case, context)
        return

    # 2. Generic auth (--auth CLI or [auth.basic] config), for all operations
    basic_auth = case.operation.schema.config.auth_for(operation=case.operation)
    if basic_auth is not None:
        if not _should_skip_auth_for_negative_testing(case, "Authorization", ParameterLocation.HEADER):
            apply_basic_auth(case, *basic_auth)
        return

    if case.operation.schema.auth.is_defined:
        if not case.operation.schema.is_security_param_negated(case):
            case.operation.schema.auth.set(case, context)
        return

    # 3. Spec-specific auth (OpenAPI-aware [auth.openapi.*])
    if case.operation.schema.apply_auth(case, context):
        return

    # 4. Global auth
    if GLOBAL_AUTH_STORAGE.is_defined and not case.operation.schema.is_security_param_negated(case):
        GLOBAL_AUTH_STORAGE.set(case, context)


def set_learned_on_case(case: Case) -> None:
    """Attach credentials for security the schema learned after `case` was generated.

    Values already on the case win: if the credentials would replace any of them, the case is left unchanged.
    """
    if case._has_explicit_auth:
        return
    schema = case.operation.schema
    # These sources take precedence over the schema's security and were already applied at generation time.
    if schema.config.auth_for(operation=case.operation) is not None or schema.auth.is_defined:
        return
    before = _credential_containers(case)
    if not schema.apply_auth(case, AuthContext(operation=case.operation, app=case.operation.app)):
        return
    with_auth = _credential_containers(case)
    _fill_credential_containers(case, before)
    if any(
        with_auth[location].get(name) != value for location, values in before.items() for name, value in values.items()
    ):
        case._has_explicit_auth = False
        return
    # Edits made after generation must still count as edits once the credentials are added.
    case._check_modifications()
    _fill_credential_containers(case, with_auth)
    case._sync_validated_hashes()


def _credential_containers(case: Case) -> dict[ParameterLocation, dict[str, JsonValue]]:
    containers = {}
    for location in (ParameterLocation.HEADER, ParameterLocation.QUERY, ParameterLocation.COOKIE):
        container = case.get_container(location)
        if isinstance(container, MutableMapping):
            containers[location] = dict(container)
    return containers


def _fill_credential_containers(case: Case, containers: dict[ParameterLocation, dict[str, JsonValue]]) -> None:
    for location, values in containers.items():
        container = case.get_container(location)
        assert isinstance(container, MutableMapping)
        container.clear()
        container.update(values)


GLOBAL_AUTH_STORAGE: AuthStorage = AuthStorage()
unregister = GLOBAL_AUTH_STORAGE.unregister


def _caching_providers_in(storage: AuthStorage) -> Iterator[CachingAuthProvider]:
    from schemathesis.wfc.escalation import EscalatingAuthProvider

    for provider in storage.providers:
        if isinstance(provider, SelectiveAuthProvider):
            provider = provider.provider
        # Reach into per-identity entries, or reactive reauth finds no cached tokens to refresh.
        if isinstance(provider, EscalatingAuthProvider):
            yield from (entry for entry in provider.providers if isinstance(entry, CachingAuthProvider))
        elif isinstance(provider, CachingAuthProvider):
            yield provider


def _iter_caching_providers(case: Case) -> Iterator[CachingAuthProvider]:
    """Caching auth providers for this case: its own, config dynamic-token, schema-level and global ``@auth``."""
    if case._auth_storage is not None:
        yield from _caching_providers_in(case._auth_storage)
    for provider in case.operation.schema.security_auth_providers():
        if isinstance(provider, CachingAuthProvider):
            yield provider
    yield from _caching_providers_in(case.operation.schema.auth)
    yield from _caching_providers_in(GLOBAL_AUTH_STORAGE)


def apply_configured_auth(case: Case) -> None:
    """Set the credentials a run would send for `case`: auth providers, configured auth or the schema's security.

    A case generated with its own auth storage gets credentials from that storage only.
    """
    set_on_case(case, AuthContext(operation=case.operation, app=case.operation.app), case._auth_storage)


def refresh_auth(case: Case) -> None:
    """Invalidate cached auth for `case` and re-apply it, so the next request carries a fresh token."""
    # Concurrent callers share one refetch behind the provider's lock; invalidating only expires the cache.
    for provider in _iter_caching_providers(case):
        provider.invalidate()
    set_on_case(case, AuthContext(operation=case.operation, app=case.operation.app, _reauth=True), case._auth_storage)


@dataclass(slots=True)
class ReauthState:
    """Reactive-reauth state shared across worker threads."""

    retry_on_statuses: frozenset[int]
    reauth_count: int = 0
    # Set when the breaker trips; stops further reauth attempts.
    disabled: bool = False
    # A re-login failed at least once (for reporting).
    broke: bool = False
    _consecutive_failures: int = 0
    # Operations whose retry status survived a successful re-login, so the status is not about the token.
    _token_independent: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def should_retry(self, status_code: int, label: str) -> bool:
        return status_code in self.retry_on_statuses and not self.disabled and label not in self._token_independent

    def note_refresh_failure(self) -> None:
        with self._lock:
            self.broke = True
            self._consecutive_failures += 1
            if self._consecutive_failures >= REAUTH_BREAKER_THRESHOLD:
                self.disabled = True

    def note_replay(self, status_code: int, label: str, *, succeeded: bool) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if succeeded:
                self.reauth_count += 1
            elif status_code in self.retry_on_statuses:
                self._token_independent.add(label)


def _retry_on_statuses_in(storage: AuthStorage, schema: BaseSchema) -> set[int]:
    statuses: set[int] = set()
    for provider in _caching_providers_in(storage):
        if provider.retry_on is None:
            statuses.add(schema.status_code_for(StatusClass.UNAUTHENTICATED))
        else:
            statuses.update(provider.retry_on)
    return statuses


def compute_retry_on_statuses(schema: BaseSchema) -> frozenset[int]:
    """Union of `retry_on` across configured dynamic schemes and registered `@auth` providers; empty means off."""
    statuses: set[int] = set()
    for scheme in schema.config.auth.dynamic.schemes.values():
        statuses.update(scheme.retry_on)
    statuses.update(_retry_on_statuses_in(schema.auth, schema))
    statuses.update(_retry_on_statuses_in(GLOBAL_AUTH_STORAGE, schema))
    return frozenset(statuses)


def reauth_state_for(case: Case) -> ReauthState:
    """Fresh reauth state for one call of `case`, adding `retry_on` of the auth the case was generated with."""
    schema = case.operation.schema
    statuses = schema.reauth_retry_statuses
    if case._auth_storage is not None:
        statuses |= _retry_on_statuses_in(case._auth_storage, schema)
    return ReauthState(retry_on_statuses=statuses)


def reauth_and_replay(case: Case, response: Response, state: ReauthState, recall: Callable[[], Response]) -> Response:
    """Refresh auth and replay the request once via `recall` if `response` signals an expired token.

    Return the replay, or `response` when nothing was refreshed; negated-security cases keep their expected 401.
    """
    if not (
        state.should_retry(response.status_code, case.operation.label)
        and case._has_explicit_auth
        and not case.operation.schema.is_security_param_negated(case)
    ):
        return response
    try:
        refresh_auth(case)
    except AuthenticationError:
        state.note_refresh_failure()
        return response
    try:
        replay = recall()
    except Exception:
        # Replay could not complete (e.g. transport error); keep the response we already have.
        return response
    succeeded = case.operation.schema.classify_status(replay.status_code) is StatusClass.SUCCESS
    state.note_replay(replay.status_code, case.operation.label, succeeded=succeeded)
    return replay


def auth(
    *,
    refresh_interval: int | None = DEFAULT_REFRESH_INTERVAL,
    cache_by_key: CacheKeyFunction | None = None,
    retry_on: list[int] | None = None,
) -> FilterableRegisterAuth:
    """Register a dynamic authentication provider for APIs with expiring tokens.

    Args:
        refresh_interval: Seconds between token refreshes. Default is `300`. Use `None` to disable caching
        cache_by_key: Function to generate cache keys for different auth contexts (e.g., OAuth scopes)
        retry_on: Status codes that trigger a cache invalidation and token refetch. Default is the protocol's
            unauthenticated status: `401` for HTTP, `UNAUTHENTICATED` (`16`) for gRPC.
            Use `[]` to disable.

    Example:
        ```python
        import schemathesis
        import requests

        @schemathesis.auth()
        class TokenAuth:
            def get(self, case, context):
                \"\"\"Fetch fresh authentication token\"\"\"
                response = requests.post(
                    "http://localhost:8000/auth/token",
                    json={"username": "demo", "password": "test"}
                )
                return response.json()["access_token"]

            def set(self, case, data, context):
                \"\"\"Apply token to test case headers\"\"\"
                case.headers = case.headers or {}
                case.headers["Authorization"] = f"Bearer {data}"
        ```

    """
    return GLOBAL_AUTH_STORAGE.auth(refresh_interval=refresh_interval, cache_by_key=cache_by_key, retry_on=retry_on)


auth.__dict__ = GLOBAL_AUTH_STORAGE.auth.__dict__
auth.set_from_requests = GLOBAL_AUTH_STORAGE.set_from_requests  # type: ignore[attr-defined]
