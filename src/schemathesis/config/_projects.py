from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from schemathesis.config._auth import AuthConfig
from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve
from schemathesis.config._error import ConfigError
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._operations import OperationConfig, OperationsConfig
from schemathesis.config._output import OutputConfig
from schemathesis.config._parameters import ParameterOverride, load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.config._rate_limit import build_limiter
from schemathesis.config._report import ReportsConfig
from schemathesis.core import hooks
from schemathesis.core.validation import validate_base_url

if TYPE_CHECKING:
    import hypothesis
    from pyrate_limiter import Limiter

    from schemathesis.config import SchemathesisConfig
    from schemathesis.schemas import APIOperation

DEFAULT_WORKERS = 1


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
    _parent: SchemathesisConfig | None
    base_url: str | None
    headers: dict | None
    hooks: str | None
    proxy: str | None
    workers: int
    max_response_time: float | int | None
    exclude_deprecated: bool | None
    continue_on_failure: bool | None
    tls_verify: bool | str | None
    rate_limit: Limiter | None
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
        "_parent",
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
        "_rate_limit",
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
        parent: SchemathesisConfig | None = None,
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
        self._parent = parent
        if base_url is not None:
            _validate_base_url(base_url)
        self.base_url = base_url
        self.headers = headers
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
            self.rate_limit = build_limiter(rate_limit)
        else:
            self.rate_limit = rate_limit
        self._rate_limit = rate_limit
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
            headers={resolve(key, key): resolve(value, value) for key, value in data.get("headers", {}).items()}
            if "headers" in data
            else None,
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
    def discover(cls) -> ProjectConfig:
        from schemathesis.config import SchemathesisConfig

        return SchemathesisConfig.discover().projects.default

    def set(
        self,
        *,
        base_url: str | None = None,
        headers: dict | None = None,
        auth: tuple[str, str] | None = None,
        workers: int | Literal["auto"] = "auto",
        continue_on_failure: bool | None = None,
        rate_limit: str | None = None,
        request_timeout: float | int | None = None,
        tls_verify: bool | str | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        proxy: str | None = None,
    ) -> None:
        if base_url is not None:
            _validate_base_url(base_url)
            self.base_url = base_url

        if headers is not None:
            _headers = self.headers or {}
            _headers.update(headers)
            self.headers = _headers

        if isinstance(workers, int):
            self.workers = workers
        else:
            self.workers = get_workers_count()

        if continue_on_failure is not None:
            self.continue_on_failure = continue_on_failure

        if rate_limit is not None:
            self.rate_limit = build_limiter(rate_limit)

        if request_timeout is not None:
            self.request_timeout = request_timeout

        if tls_verify is not None:
            self.tls_verify = tls_verify

        if request_cert is not None:
            self.request_cert = request_cert

        if request_cert_key is not None:
            self.request_cert_key = request_cert_key

        if proxy is not None:
            self.proxy = proxy

    def parameters_for(self, *, operation: APIOperation | None = None) -> dict:
        parameters = {name: param.value for name, param in self.parameters.items()}
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            parameters.update({name: param.value for name, param in config.parameters.items()})
        return parameters

    def auth_for(self, *, operation: APIOperation | None = None) -> tuple[str, str] | None:
        auth = None
        if self.auth.basic is not None:
            auth = self.auth.basic["username"], self.auth.basic["password"]
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.auth.basic is not None:
                auth = config.auth.basic["username"], config.auth.basic["password"]
        return auth

    def headers_for(self, *, operation: APIOperation | None = None) -> dict[str, str] | None:
        headers = None
        if self.headers is not None:
            headers = self.headers
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.headers is not None:
                headers = config.headers
        return headers

    def generation_for(
        self,
        *,
        operation: APIOperation | None = None,
        phase: Literal["examples", "coverage", "fuzzing", "stateful"] | None = None,
    ) -> GenerationConfig:
        configs = []
        if operation is not None:
            for op in self.operations.operations:
                if op._filter_set.applies_to(operation=operation):
                    if phase is not None:
                        phase_config = op.phases.get_by_name(name=phase)
                        configs.append(phase_config.generation)
                    configs.append(op.generation)
        if phase is not None:
            phase_config = self.phases.get_by_name(name=phase)
            configs.append(phase_config.generation)
        configs.append(self.generation)
        return GenerationConfig.from_hierarchy(configs)

    def checks_config_for(
        self,
        *,
        operation: APIOperation | None = None,
        phase: Literal["examples", "coverage", "fuzzing", "stateful"] | None = None,
    ) -> ChecksConfig:
        configs = []
        if operation is not None:
            for op in self.operations.operations:
                if op._filter_set.applies_to(operation=operation):
                    if phase is not None:
                        phase_config = op.phases.get_by_name(name=phase)
                        configs.append(phase_config.checks)
                    configs.append(op.checks)
        if phase is not None:
            phase_config = self.phases.get_by_name(name=phase)
            configs.append(phase_config.checks)
        configs.append(self.checks)
        return ChecksConfig.from_hierarchy(configs)

    def get_hypothesis_settings(self) -> hypothesis.settings:
        # TODO: rework so it accepts optional operation / phase too
        import hypothesis

        # "database",
        # "phases",
        # "stateful_step_count",
        # "suppress_health_check",
        # "deadline",
        kwargs: dict[str, Any] = {
            "derandomize": self.generation.deterministic,
            "deadline": None,
        }
        if self.generation.max_examples is not None:
            kwargs["max_examples"] = self.generation.max_examples
        # TODO: prepare
        # suppress_health_check = self.run.suppress_health_check
        # TODO: Prepare DB settings
        # database = self.project.generation.database
        # Prepare phases

        return hypothesis.settings(**kwargs)

    def _get_parent(self) -> SchemathesisConfig:
        if self._parent is None:
            from schemathesis.config import SchemathesisConfig

            self._parent = SchemathesisConfig.discover()
        return self._parent

    @property
    def output(self) -> OutputConfig:
        return self._get_parent().output

    @property
    def wait_for_schema(self) -> float | int | None:
        return self._get_parent().wait_for_schema

    @property
    def max_failures(self) -> int | None:
        return self._get_parent().max_failures

    @max_failures.setter
    def max_failures(self, value: int) -> None:
        parent = self._get_parent()
        parent.max_failures = value

    @property
    def reports(self) -> ReportsConfig:
        return self._get_parent().reports

    @property
    def seed(self) -> int:
        return self._get_parent().seed

    @seed.setter
    def seed(self, value: int) -> None:
        parent = self._get_parent()
        parent._seed = value


def _validate_base_url(base_url: str) -> None:
    try:
        validate_base_url(base_url)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None


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

    def _set_parent(self, parent: SchemathesisConfig) -> None:
        self.default._parent = parent
        for project in self.named.values():
            project._parent = parent
        self.override._parent = parent

    def get_default(self) -> ProjectConfig:
        config = ProjectConfig.from_hierarchy([self.override, self.default])
        config._parent = self.default._parent
        return config

    def get(self, schema: dict[str, Any]) -> ProjectConfig:
        # Highest priority goes to `override`, then config specifically
        # for the given project, then the "default" project config
        configs = [self.override]
        title = schema.get("info", {}).get("title")
        if title is not None:
            named = self.named.get(title)
            if named is not None:
                configs.append(named)
        configs.append(self.default)
        config = ProjectConfig.from_hierarchy(configs)
        config._parent = self.default._parent
        return config
