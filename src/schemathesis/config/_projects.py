from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from schemathesis.config._auth import AuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError, validate_rate_limit
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._operations import OperationConfig
from schemathesis.config._parameters import ParameterOverride, load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.core import hooks
from schemathesis.core.validation import validate_base_url

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


@dataclass(repr=False)
class ConfigOverride:
    checks: ChecksConfig | None

    __slots__ = ("checks",)


@dataclass(repr=False)
class ProjectConfig(DiffBase):
    _override: ConfigOverride | None
    base_url: str | None
    headers: dict
    hooks: str | None
    proxy: str | None
    workers: int | str
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
    operations: list[OperationConfig]

    __slots__ = (
        "_override",
        "base_url",
        "headers",
        "hooks",
        "proxy",
        "workers",
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
        "operations",
    )

    def __init__(
        self,
        *,
        base_url: str | None = None,
        headers: dict | None = None,
        hooks_: str | None = None,
        workers: int | str = "auto",
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
        operations: list[OperationConfig] | None = None,
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
        self.workers = workers
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
        self.operations = operations or []

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        return cls(
            base_url=resolve(data.get("base-url"), None),
            headers={resolve(key, key): resolve(value, value) for key, value in data.get("headers", {}).items()},
            hooks_=resolve(data.get("hooks"), None),
            workers=data.get("workers", "auto"),
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
            operations=[OperationConfig.from_dict(operation) for operation in data.get("operations", [])],
        )

    def override(
        self, *, base_url: str | None, included_check_names: list[str] | None, excluded_check_names: list[str] | None
    ) -> None:
        if base_url is not None:
            self.base_url = base_url
        if not self._override:
            self._override = ConfigOverride(checks=None)

        if self._override.checks is None:
            self._override.checks = ChecksConfig()

        self._override.checks.override(
            included_check_names=included_check_names, excluded_check_names=excluded_check_names
        )

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
            for op in self.operations:
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

    __slots__ = ("default", "named")

    def __init__(
        self,
        *,
        default: ProjectConfig | None = None,
        named: dict[str, ProjectConfig] | None = None,
    ) -> None:
        self.default = default or ProjectConfig()
        self.named = named or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectsConfig:
        return cls(
            default=ProjectConfig.from_dict(data),
            named={project["title"]: ProjectConfig.from_dict(project) for project in data.get("project", [])},
        )
