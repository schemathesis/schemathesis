from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from schemathesis.config._auth import AuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError, validate_rate_limit
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._operations import OperationConfig, OperationsConfig
from schemathesis.config._parameters import ParameterOverride, load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.core import hooks
from schemathesis.core.validation import validate_base_url

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

DEFAULT_WORKERS = 1


@dataclass(repr=False)
class ConfigOverride:
    checks: ChecksConfig | None

    __slots__ = ("checks",)


def get_workers_count() -> int:
    """Detect the number of available CPUs for the current process, if possible.

    Use ``DEFAULT_WORKERS`` if not possible to detect.
    """
    if hasattr(os, "sched_getaffinity"):
        # In contrast with `os.cpu_count` this call respects limits on CPU resources on some Unix systems
        return len(os.sched_getaffinity(0))
    # Number of CPUs in the system, or 1 if undetermined
    return os.cpu_count() or DEFAULT_WORKERS


@dataclass(repr=False)
class ProjectConfig(DiffBase):
    _override: ConfigOverride | None
    base_url: str | None
    headers: dict
    hooks: str | None
    proxy: str | None
    workers: int
    max_response_time: float | int | None
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
    operations: OperationsConfig

    __slots__ = (
        "_override",
        "base_url",
        "headers",
        "hooks",
        "proxy",
        "workers",
        "max_response_time",
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
        "operations",
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        headers: dict | None = None,
        hooks_: str | None = None,
        workers: int | Literal["auto"] = "auto",
        proxy: str | None = None,
        max_response_time: float | int | None = None,
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
        operations: OperationsConfig | None = None,
    ) -> None:
        self._override = None
        if base_url is not None:
            try:
                validate_base_url(base_url)
            except ValueError as exc:
                raise ConfigError(str(exc)) from None
        self.base_url = base_url
        self.headers = headers or {}
        self.hooks = hooks_
        if hooks_:
            hooks.load_from_path(hooks_)
        else:
            hooks.load_from_env()
        if isinstance(workers, int):
            self.workers = workers
        else:
            self.workers = get_workers_count()
        self.proxy = proxy
        self.max_response_time = max_response_time
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
        self.operations = operations or OperationsConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        return cls(
            base_url=resolve(data.get("base-url"), None),
            headers={resolve(key, key): resolve(value, value) for key, value in data.get("headers", {}).items()},
            hooks_=resolve(data.get("hooks"), None),
            workers=data.get("workers", "auto"),
            proxy=resolve(data.get("proxy"), None),
            max_response_time=data.get("max-response-time"),
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
            operations=OperationsConfig(
                operations=[OperationConfig.from_dict(operation) for operation in data.get("operations", [])]
            ),
        )

    @classmethod
    def from_many(cls, configs: list[ProjectConfig]) -> ProjectConfig:
        raise NotImplementedError

    def set(
        self,
        *,
        base_url: str | None = None,
        headers: dict | None = None,
        auth: tuple[str, str] | None = None,
        workers: int | Literal["auto"] = "auto",
        wait_for_schema: float | int | None = None,
        continue_on_failure: bool | None = None,
        rate_limit: str | None = None,
        request_timeout: float | int | None = None,
        tls_verify: bool | str | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        proxy: str | None = None,
    ) -> None:
        if base_url is not None:
            self.base_url = base_url

    def checks_config_for(
        self,
        *,
        operation: APIOperation | None = None,
        phase: Literal["examples", "coverage", "fuzzing", "stateful"] | None = None,
    ) -> ChecksConfig:
        configs = []
        if self._override is not None and self._override.checks is not None:
            configs.append(self._override.checks)
        if operation is not None:
            for op in self.operations.operations:
                if op.filter_set.applies_to(operation=operation):
                    if phase is not None:
                        phase_config = op.phases.get_by_name(name=phase)
                        configs.append(phase_config.checks)
                    configs.append(op.checks)
        if phase is not None:
            phase_config = self.phases.get_by_name(name=phase)
            configs.append(phase_config.checks)
        configs.append(self.checks)
        return ChecksConfig.from_many(configs)


@dataclass(repr=False)
class ProjectsConfig(DiffBase):
    default: ProjectConfig
    named: dict[str, ProjectConfig]
    override: ProjectConfig

    __slots__ = ("default", "named", "override")

    def __init__(
        self,
        *,
        default: ProjectConfig | None = None,
        named: dict[str, ProjectConfig] | None = None,
    ) -> None:
        self.default = default or ProjectConfig()
        self.named = named or {}
        self.override = ProjectConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectsConfig:
        return cls(
            default=ProjectConfig.from_dict(data),
            named={project["title"]: ProjectConfig.from_dict(project) for project in data.get("project", [])},
        )

    def get(self, schema: dict[str, Any]) -> ProjectConfig:
        configs = [self.override]
        title = schema.get("info", {}).get("title")
        if title is not None:
            named = self.named.get(title)
            if named is not None:
                configs.append(named)
        configs.append(self.default)
        return ProjectConfig.from_many(configs)
