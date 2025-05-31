from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Generator

from schemathesis.config._auth import AuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._parameters import load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.config._rate_limit import build_limiter
from schemathesis.config._warnings import SchemathesisWarning, resolve_warnings
from schemathesis.core.errors import IncorrectUsage
from schemathesis.filters import FilterSet, HasAPIOperation, expression_to_filter_function, is_deprecated

if TYPE_CHECKING:
    from pyrate_limiter import Limiter

    from schemathesis.schemas import APIOperation

FILTER_ATTRIBUTES = [
    ("name", "name"),
    ("method", "method"),
    ("path", "path"),
    ("tag", "tag"),
    ("operation-id", "operation_id"),
]


@contextmanager
def reraise_filter_error(attr: str) -> Generator:
    try:
        yield
    except IncorrectUsage as exc:
        if str(exc) == "Filter already exists":
            raise ConfigError(
                f"Filter for '{attr}' already exists. You can't simultaneously include and exclude the same thing."
            ) from None
        raise
    except re.error as exc:
        raise ConfigError(
            f"Filter for '{attr}' contains an invalid regular expression: {exc.pattern!r}\n\n  {exc}"
        ) from None


@dataclass
class OperationsConfig(DiffBase):
    operations: list[OperationConfig]

    __slots__ = ("operations",)

    def __init__(self, *, operations: list[OperationConfig] | None = None):
        self.operations = operations or []

    def __repr__(self) -> str:
        if self.operations:
            return f"[{', '.join(DiffBase.__repr__(cfg) for cfg in self.operations)}]"
        return "[]"

    @classmethod
    def from_hierarchy(cls, configs: list[OperationsConfig]) -> OperationsConfig:  # type: ignore
        return cls(operations=sum([config.operations for config in reversed(configs)], []))

    def get_for_operation(self, operation: APIOperation) -> OperationConfig:
        configs = [config for config in self.operations if config._filter_set.applies_to(operation)]
        return OperationConfig.from_hierarchy(configs)

    def create_filter_set(
        self,
        *,
        include_path: tuple[str, ...],
        include_method: tuple[str, ...],
        include_name: tuple[str, ...],
        include_tag: tuple[str, ...],
        include_operation_id: tuple[str, ...],
        include_path_regex: str | None,
        include_method_regex: str | None,
        include_name_regex: str | None,
        include_tag_regex: str | None,
        include_operation_id_regex: str | None,
        exclude_path: tuple[str, ...],
        exclude_method: tuple[str, ...],
        exclude_name: tuple[str, ...],
        exclude_tag: tuple[str, ...],
        exclude_operation_id: tuple[str, ...],
        exclude_path_regex: str | None,
        exclude_method_regex: str | None,
        exclude_name_regex: str | None,
        exclude_tag_regex: str | None,
        exclude_operation_id_regex: str | None,
        include_by: Callable | None,
        exclude_by: Callable | None,
        exclude_deprecated: bool,
    ) -> FilterSet:
        # Build explicit include filters
        include_set = FilterSet()
        if include_by:
            include_set.include(include_by)
        for name_ in include_name:
            include_set.include(name=name_)
        for method in include_method:
            include_set.include(method=method)
        for path in include_path:
            include_set.include(path=path)
        for tag in include_tag:
            include_set.include(tag=tag)
        for operation_id in include_operation_id:
            include_set.include(operation_id=operation_id)
        if (
            include_name_regex
            or include_method_regex
            or include_path_regex
            or include_tag_regex
            or include_operation_id_regex
        ):
            include_set.include(
                name_regex=include_name_regex,
                method_regex=include_method_regex,
                path_regex=include_path_regex,
                tag_regex=include_tag_regex,
                operation_id_regex=include_operation_id_regex,
            )

        # Build explicit exclude filters
        exclude_set = FilterSet()
        if exclude_by:
            exclude_set.include(exclude_by)
        for name_ in exclude_name:
            exclude_set.include(name=name_)
        for method in exclude_method:
            exclude_set.include(method=method)
        for path in exclude_path:
            exclude_set.include(path=path)
        for tag in exclude_tag:
            exclude_set.include(tag=tag)
        for operation_id in exclude_operation_id:
            exclude_set.include(operation_id=operation_id)
        if (
            exclude_name_regex
            or exclude_method_regex
            or exclude_path_regex
            or exclude_tag_regex
            or exclude_operation_id_regex
        ):
            exclude_set.include(
                name_regex=exclude_name_regex,
                method_regex=exclude_method_regex,
                path_regex=exclude_path_regex,
                tag_regex=exclude_tag_regex,
                operation_id_regex=exclude_operation_id_regex,
            )

        # Add deprecated operations to exclude filters if requested
        if exclude_deprecated:
            exclude_set.include(is_deprecated)

        # Also update operations list for consistency with config structure
        if not include_set.is_empty():
            self.operations.insert(0, OperationConfig(filter_set=include_set, enabled=True))
        if not exclude_set.is_empty():
            self.operations.insert(0, OperationConfig(filter_set=exclude_set, enabled=False))

        final = FilterSet()

        # Get a stable reference to operations
        operations = list(self.operations)

        # Define a closure that implements our priority logic
        def priority_filter(ctx: HasAPIOperation) -> bool:
            """Filter operations according to CLI and config priority."""
            # 1. CLI includes override everything if present
            if not include_set.is_empty():
                return include_set.match(ctx)

            # 2. CLI excludes take precedence over config
            if not exclude_set.is_empty() and exclude_set.match(ctx):
                return False

            # 3. Check config operations in priority order (first match wins)
            for op_config in operations:
                if op_config._filter_set.match(ctx):
                    return op_config.enabled

            # 4. Default to include if no rule matches
            return True

        # Add our priority function as the filter
        final.include(priority_filter)

        return final


@dataclass
class OperationConfig(DiffBase):
    _filter_set: FilterSet
    enabled: bool
    headers: dict | None
    proxy: str | None
    continue_on_failure: bool | None
    tls_verify: bool | str | None
    rate_limit: Limiter | None
    request_timeout: float | int | None
    request_cert: str | None
    request_cert_key: str | None
    parameters: dict[str, Any]
    warnings: list[SchemathesisWarning] | None
    auth: AuthConfig
    checks: ChecksConfig
    phases: PhasesConfig
    generation: GenerationConfig

    __slots__ = (
        "_filter_set",
        "enabled",
        "headers",
        "proxy",
        "continue_on_failure",
        "tls_verify",
        "rate_limit",
        "_rate_limit",
        "request_timeout",
        "request_cert",
        "request_cert_key",
        "parameters",
        "warnings",
        "auth",
        "checks",
        "phases",
        "generation",
    )

    def __init__(
        self,
        *,
        filter_set: FilterSet | None = None,
        enabled: bool = True,
        headers: dict | None = None,
        proxy: str | None = None,
        continue_on_failure: bool | None = None,
        tls_verify: bool | str | None = None,
        rate_limit: str | None = None,
        request_timeout: float | int | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        parameters: dict[str, Any] | None = None,
        warnings: bool | list[SchemathesisWarning] | None = None,
        auth: AuthConfig | None = None,
        checks: ChecksConfig | None = None,
        phases: PhasesConfig | None = None,
        generation: GenerationConfig | None = None,
    ) -> None:
        self._filter_set = filter_set or FilterSet()
        self.enabled = enabled
        self.headers = headers
        self.proxy = proxy
        self.continue_on_failure = continue_on_failure
        self.tls_verify = tls_verify
        if rate_limit is not None:
            self.rate_limit = build_limiter(rate_limit)
        else:
            self.rate_limit = rate_limit
        self._rate_limit = rate_limit
        self.request_timeout = request_timeout
        self.request_cert = request_cert
        self.request_cert_key = request_cert_key
        self.parameters = parameters or {}
        self._set_warnings(warnings)
        self.auth = auth or AuthConfig()
        self.checks = checks or ChecksConfig()
        self.phases = phases or PhasesConfig()
        self.generation = generation or GenerationConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationConfig:
        filter_set = FilterSet()
        for key_suffix, arg_suffix in (("", ""), ("-regex", "_regex")):
            for attr, arg_name in FILTER_ATTRIBUTES:
                key = f"include-{attr}{key_suffix}"
                if key in data:
                    with reraise_filter_error(attr):
                        filter_set.include(**{f"{arg_name}{arg_suffix}": data[key]})
                key = f"exclude-{attr}{key_suffix}"
                if key in data:
                    with reraise_filter_error(attr):
                        filter_set.exclude(**{f"{arg_name}{arg_suffix}": data[key]})
        for key, method in (("include-by", filter_set.include), ("exclude-by", filter_set.exclude)):
            if key in data:
                expression = data[key]
                try:
                    func = expression_to_filter_function(expression)
                    method(func)
                except ValueError:
                    raise ConfigError(f"Invalid filter expression: '{expression}'") from None

        return cls(
            filter_set=filter_set,
            enabled=data.get("enabled", True),
            headers={resolve(key): resolve(value) for key, value in data.get("headers", {}).items()}
            if "headers" in data
            else None,
            proxy=resolve(data.get("proxy")),
            continue_on_failure=data.get("continue-on-failure", None),
            tls_verify=resolve(data.get("tls-verify")),
            rate_limit=resolve(data.get("rate-limit")),
            request_timeout=data.get("request-timeout"),
            request_cert=resolve(data.get("request-cert")),
            request_cert_key=resolve(data.get("request-cert-key")),
            parameters=load_parameters(data),
            warnings=resolve_warnings(data.get("warnings")),
            auth=AuthConfig.from_dict(data.get("auth", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            phases=PhasesConfig.from_dict(data.get("phases", {})),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
        )

    def _set_warnings(self, warnings: bool | list[SchemathesisWarning] | None) -> None:
        if warnings is False:
            self.warnings = []
        elif warnings is True:
            self.warnings = list(SchemathesisWarning)
        else:
            self.warnings = warnings
