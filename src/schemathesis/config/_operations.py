from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

from schemathesis.config._auth import AuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError, validate_rate_limit
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._parameters import ParameterOverride, load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.core.errors import IncorrectUsage
from schemathesis.filters import FilterSet, expression_to_filter_function

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

    def set(
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
        include_by: str | None,
        exclude_by: str | None,
        exclude_deprecated: bool,
    ) -> None:
        pass


@dataclass
class OperationConfig(DiffBase):
    filter_set: FilterSet
    enabled: bool
    base_url: str | None
    headers: dict
    proxy: str | None
    max_response_time: float | int | None
    wait_for_schema: float | int | None
    exclude_deprecated: bool | None
    continue_on_failure: bool | None
    tls_verify: bool | str | None
    rate_limit: str | None
    request_timeout: float | int | None
    request_cert: str | None
    request_cert_key: str | None
    parameters: dict[str, ParameterOverride]
    auth: AuthConfig
    checks: ChecksConfig
    phases: PhasesConfig
    generation: GenerationConfig

    __slots__ = (
        "filter_set",
        "enabled",
        "base_url",
        "headers",
        "proxy",
        "max_response_time",
        "wait_for_schema",
        "exclude_deprecated",
        "continue_on_failure",
        "tls_verify",
        "rate_limit",
        "request_timeout",
        "request_cert",
        "request_cert_key",
        "parameters",
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
        base_url: str | None = None,
        headers: dict | None = None,
        proxy: str | None = None,
        max_response_time: float | int | None = None,
        wait_for_schema: float | int | None = None,
        exclude_deprecated: bool | None = None,
        continue_on_failure: bool | None = None,
        tls_verify: bool | str | None = None,
        rate_limit: str | None = None,
        request_timeout: float | int | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        parameters: dict[str, ParameterOverride] | None = None,
        auth: AuthConfig | None = None,
        checks: ChecksConfig | None = None,
        phases: PhasesConfig | None = None,
        generation: GenerationConfig | None = None,
    ) -> None:
        self.filter_set = filter_set or FilterSet()
        self.enabled = enabled
        self.base_url = base_url
        self.headers = headers or {}
        self.proxy = proxy
        self.max_response_time = max_response_time
        self.wait_for_schema = wait_for_schema
        self.exclude_deprecated = exclude_deprecated
        self.continue_on_failure = continue_on_failure
        self.tls_verify = tls_verify
        if rate_limit is not None:
            validate_rate_limit(rate_limit)
        self.rate_limit = rate_limit
        self.request_timeout = request_timeout
        self.request_cert = request_cert
        self.request_cert_key = request_cert_key
        self.parameters = parameters or {}
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
            base_url=resolve(data.get("base-url"), None),
            headers={resolve(key, key): resolve(value, value) for key, value in data.get("headers", {}).items()},
            proxy=resolve(data.get("proxy"), None),
            max_response_time=data.get("max-response-time"),
            wait_for_schema=data.get("wait-for-schema"),
            exclude_deprecated=data.get("exclude-deprecated"),
            continue_on_failure=data.get("continue-on-failure"),
            tls_verify=resolve(data.get("tls-verify"), None),
            rate_limit=resolve(data.get("rate-limit"), None),
            request_timeout=data.get("request-timeout"),
            request_cert=resolve(data.get("request-cert"), None),
            request_cert_key=resolve(data.get("request-cert-key"), None),
            parameters=load_parameters(data),
            auth=AuthConfig.from_dict(data.get("auth", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            phases=PhasesConfig.from_dict(data.get("phases", {})),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
        )

    def __repr__(self) -> str:
        return super().__repr__()
