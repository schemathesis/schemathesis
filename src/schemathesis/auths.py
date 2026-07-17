"""Support for custom API authentication mechanisms."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Protocol,
    TypeVar,
    overload,
    runtime_checkable,
)

from schemathesis.config._auth import DEFAULT_RETRY_ON
from schemathesis.core.errors import AuthenticationError, IncorrectUsage
from schemathesis.core.marks import Mark
from schemathesis.core.parameters import ParameterLocation
from schemathesis.filters import FilterSet, FilterValue, MatcherFunc, attach_filter_chain
from schemathesis.generation.meta import CoveragePhaseData, FuzzingPhaseData, StatefulPhaseData

if TYPE_CHECKING:
    import requests.auth

    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, BaseSchema

DEFAULT_REFRESH_INTERVAL = 300
# Consecutive non-recovering reauth attempts before the breaker trips and disables reauth for the run. Not configurable.
REAUTH_BREAKER_THRESHOLD = 3
# Consecutive failed token fetches before a caching provider stops hammering the login endpoint. Not configurable.
TOKEN_FETCH_BREAKER_THRESHOLD = 3
AuthStorageMark = Mark["AuthStorage"](attr_name="auth_storage")
Auth = TypeVar("Auth")


@dataclass(slots=True)
class AuthContext:
    """Runtime context passed to authentication providers during token generation.

    Provides access to the current API operation and application instance when
    auth providers need operation-specific tokens or application state.

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


CacheKeyFunction = Callable[["Case", "AuthContext"], str | int]


@runtime_checkable
class AuthProvider(Generic[Auth], Protocol):
    """Protocol for implementing custom authentication in API tests."""

    def get(self, case: Case, ctx: AuthContext) -> Auth | None:
        """Obtain authentication data for the test case.

        Args:
            case: Generated test case requiring authentication.
            ctx: Authentication state and configuration.

        Returns:
            Authentication data (e.g., token, credentials) or `None`.

        """
        ...  # pragma: no cover

    def set(self, case: Case, data: Auth, ctx: AuthContext) -> None:
        """Apply authentication data to the test case.

        Args:
            case: Test case to modify.
            data: Authentication data from the `get` method.
            ctx: Authentication state and configuration.

        """
        ...  # pragma: no cover


@dataclass
class CacheEntry(Generic[Auth]):
    """Cached auth data."""

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
    """Caches the underlying auth provider."""

    provider: AuthProvider
    refresh_interval: int = DEFAULT_REFRESH_INTERVAL
    cache_entry: CacheEntry[Auth] | None = None
    # The timer exists here to simplify testing
    timer: Callable[[], float] = time.monotonic
    # Status codes that should trigger a cache invalidation + refetch on the next request.
    retry_on: list[int] = field(default_factory=lambda: list(DEFAULT_RETRY_ON))
    _refresh_lock: threading.Lock = field(default_factory=threading.Lock)
    _fetch_failures: dict[str | int | None, int] = field(default_factory=dict)
    _fetch_disabled_keys: set[str | int | None] = field(default_factory=set)

    def get(self, case: Case, context: AuthContext) -> Auth | None:
        """Get cached auth value."""
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
                if key in self._fetch_disabled_keys:
                    raise AuthenticationError(
                        self.provider.__class__.__name__,
                        "get",
                        f"Token fetch failed {TOKEN_FETCH_BREAKER_THRESHOLD} times in a row; not retrying for this run",
                    )
                # We know that optional auth is possible only inside a higher-level wrapper
                try:
                    data: Auth = self.provider.get(case, context)  # type: ignore[assignment]
                except AuthenticationError:
                    self._note_fetch_failure(key)
                    raise
                except Exception as exc:
                    self._note_fetch_failure(key)
                    provider_name = self.provider.__class__.__name__
                    raise AuthenticationError(
                        provider_name, "get", str(exc), show_traceback=True, include_provider_context=True
                    ) from exc
                self._fetch_failures.pop(key, None)
                self._set_cache_entry(data, case, context)
                return data
        return cache_entry.data

    def _fetch_key(self, case: Case, context: AuthContext) -> str | int | None:
        return None

    def _note_fetch_failure(self, key: str | int | None) -> None:
        count = self._fetch_failures.get(key, 0) + 1
        self._fetch_failures[key] = count
        if count >= TOKEN_FETCH_BREAKER_THRESHOLD:
            self._fetch_disabled_keys.add(key)

    def _get_cache_entry(self, case: Case, context: AuthContext) -> CacheEntry[Auth] | None:
        return self.cache_entry

    def _set_cache_entry(self, data: Auth, case: Case, context: AuthContext) -> None:
        self.cache_entry = CacheEntry(data=data, expires=self.timer() + self.refresh_interval)

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        """Set auth data on the `Case` instance.

        This implementation delegates this to the actual provider.
        """
        self.provider.set(case, data, context)

    def invalidate(self) -> None:
        self.cache_entry = None


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
        self.cache_entries.clear()


class FilterableRegisterAuth(Protocol):
    """Protocol that adds filters to the return value of `register`."""

    def __call__(self, provider_class: type[AuthProvider]) -> type[AuthProvider]: ...  # pragma: no cover

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRegisterAuth: ...  # pragma: no cover

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRegisterAuth: ...  # pragma: no cover


class FilterableApplyAuth(Protocol):
    """Protocol that adds filters to the return value of `apply`."""

    def __call__(self, test: Callable) -> Callable: ...  # pragma: no cover

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableApplyAuth: ...  # pragma: no cover

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableApplyAuth: ...  # pragma: no cover


class FilterableRequestsAuth(Protocol):
    """Protocol that adds filters to the return value of `set_from_requests`."""

    def apply_to(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRequestsAuth: ...  # pragma: no cover

    def skip_for(
        self,
        func: MatcherFunc | None = None,
        *,
        name: FilterValue | None = None,
        name_regex: str | None = None,
        method: FilterValue | None = None,
        method_regex: str | None = None,
        path: FilterValue | None = None,
        path_regex: str | None = None,
    ) -> FilterableRequestsAuth: ...  # pragma: no cover


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
                # Already wrapped, re-raise as-is
                raise
            except Exception as exc:
                # Need to unwrap to get the actual provider class name
                provider = self.provider
                # Unwrap caching providers
                while isinstance(provider, CachingAuthProvider):
                    provider = provider.provider
                provider_name = provider.__class__.__name__
                raise AuthenticationError(
                    provider_name, "get", str(exc), show_traceback=True, include_provider_context=True
                ) from exc
        return None

    def set(self, case: Case, data: Auth, context: AuthContext) -> None:
        __tracebackhide__ = True
        self.provider.set(case, data, context)


@dataclass
class AuthStorage(Generic[Auth]):
    """Store and manage API authentication."""

    providers: list[AuthProvider] = field(default_factory=list)

    @property
    def is_defined(self) -> bool:
        """Whether there is an auth provider set."""
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
        # Apply caching if desired
        instance = provider_class()
        if refresh_interval is not None:
            resolved_retry_on = list(DEFAULT_RETRY_ON) if retry_on is None else retry_on
            if cache_by_key is None:
                provider = CachingAuthProvider(instance, refresh_interval=refresh_interval, retry_on=resolved_retry_on)
            else:
                provider = KeyedCachingAuthProvider(
                    instance,
                    refresh_interval=refresh_interval,
                    cache_by_key=cache_by_key,
                    retry_on=resolved_retry_on,
                )
        else:
            provider = instance
        # Store filters if any
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
        """Unregister the currently registered auth provider.

        No-op if there is no auth provider registered.
        """
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
    """Apply HTTP Basic authentication to a case.

    Args:
        case: Test case to apply authentication to
        username: Username for basic auth
        password: Password for basic auth

    """
    from requests.auth import _basic_auth_str

    case.headers["Authorization"] = _basic_auth_str(username, password)
    case._has_explicit_auth = True


def _should_skip_auth_for_negative_testing(case: Case, param_name: str, param_location: ParameterLocation) -> bool:
    """Check if auth should be skipped because the parameter was intentionally removed for testing."""
    meta = case.meta
    if not meta or not meta.generation.mode.is_negative:
        return False

    phase_data = meta.phase.data
    if not isinstance(phase_data, FuzzingPhaseData | CoveragePhaseData | StatefulPhaseData):
        return False

    return phase_data.parameter == param_name and phase_data.parameter_location == param_location


def set_on_case(case: Case, context: AuthContext, auth_storage: AuthStorage | None) -> None:
    """Set authentication data on this case.

    Precedence order (highest to lowest):
    1. Programmatic auth (@schemathesis.auth())
    2. Generic auth (--auth CLI or [auth.basic] config)
    3. Spec-specific auth (OpenAPI-aware [auth.openapi.*])
    4. Global auth

    If there is no auth defined, then this function is no-op.
    """
    __tracebackhide__ = True
    # 1. Programmatic auth (highest priority)
    if auth_storage is not None:
        if not case.operation.schema.is_security_param_negated(case):
            auth_storage.set(case, context)
        return

    # 2. Generic auth (CLI overrides or config - applies to all operations)
    basic_auth = case.operation.schema.config.auth_for(operation=case.operation)
    if basic_auth is not None:
        # Don't apply auth if Authorization was intentionally removed for negative testing
        if not _should_skip_auth_for_negative_testing(case, "Authorization", ParameterLocation.HEADER):
            apply_basic_auth(case, *basic_auth)
        return

    if case.operation.schema.auth.is_defined:
        if not case.operation.schema.is_security_param_negated(case):
            case.operation.schema.auth.set(case, context)
        return

    # 3. Spec-specific auth (OpenAPI-aware, more targeted)
    if case.operation.schema.apply_auth(case, context):
        return

    # 4. Global auth (fallback)
    if GLOBAL_AUTH_STORAGE.is_defined:
        if not case.operation.schema.is_security_param_negated(case):
            GLOBAL_AUTH_STORAGE.set(case, context)


# Global auth API
GLOBAL_AUTH_STORAGE: AuthStorage = AuthStorage()
unregister = GLOBAL_AUTH_STORAGE.unregister


def _caching_providers_in(storage: AuthStorage) -> Iterator[CachingAuthProvider]:
    for provider in storage.providers:
        if isinstance(provider, SelectiveAuthProvider):
            provider = provider.provider
        if isinstance(provider, CachingAuthProvider):
            yield provider


def _iter_caching_providers(case: Case) -> Iterator[CachingAuthProvider]:
    """Caching auth providers reachable for this case: config dynamic-token, schema-level and global ``@auth``."""
    for provider in case.operation.schema._security_auth_providers():
        if isinstance(provider, CachingAuthProvider):
            yield provider
    yield from _caching_providers_in(case.operation.schema.auth)
    yield from _caching_providers_in(GLOBAL_AUTH_STORAGE)


def refresh_auth(case: Case) -> None:
    """Invalidate cached auth for `case` and re-apply it, so the next request carries a fresh token."""
    # Concurrent callers coalesce onto one refetch via CachingAuthProvider.get's lock; invalidate only clears.
    for provider in _iter_caching_providers(case):
        provider.invalidate()
    set_on_case(case, AuthContext(operation=case.operation, app=case.operation.app), None)


@dataclass(slots=True)
class ReauthState:
    """Reactive-reauth state shared across worker threads."""

    retry_on_statuses: frozenset[int]
    reauth_count: int = 0
    # Breaker tripped: gate that stops further reauth attempts.
    disabled: bool = False
    # Breaker tripped at least once (for reporting).
    broke: bool = False
    _consecutive_failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def should_retry(self, status_code: int) -> bool:
        return status_code in self.retry_on_statuses and not self.disabled

    def note_refresh_failure(self) -> None:
        with self._lock:
            self._record_failure()

    def note_replay(self, status_code: int) -> None:
        with self._lock:
            if 200 <= status_code < 300:
                self._consecutive_failures = 0
                self.reauth_count += 1
            elif status_code in self.retry_on_statuses:
                self._record_failure()
            # A non-2xx, non-retry status (e.g. 403, 500) is neither recovery nor reauth failure: leave the streak.

    def _record_failure(self) -> None:
        # Caller holds `_lock`.
        self._consecutive_failures += 1
        if self._consecutive_failures >= REAUTH_BREAKER_THRESHOLD:
            self.disabled = True
            self.broke = True


def compute_retry_on_statuses(schema: BaseSchema) -> frozenset[int]:
    """Union of `retry_on` across configured dynamic schemes and registered `@auth` providers; empty means off."""
    statuses: set[int] = set()
    for scheme in schema.config.auth.dynamic.schemes.values():
        statuses.update(scheme.retry_on)
    for provider in (*_caching_providers_in(schema.auth), *_caching_providers_in(GLOBAL_AUTH_STORAGE)):
        statuses.update(provider.retry_on)
    return frozenset(statuses)


def reauth_and_replay(case: Case, response: Response, state: ReauthState, recall: Callable[[], Response]) -> Response:
    """Refresh auth and replay the request once if `response` signals an expired token.

    Returns the replay (if a refresh happened) or the original response unchanged. `recall`
    performs one fresh request. Negated-security cases keep their expected 401 and are skipped.
    """
    if not (
        state.should_retry(response.status_code)
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
    state.note_replay(replay.status_code)
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
        retry_on: Status codes that trigger a cache invalidation and token refetch. Default is `[401]`.
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
