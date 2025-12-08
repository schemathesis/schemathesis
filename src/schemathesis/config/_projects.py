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
from schemathesis.config._health_check import HealthCheck
from schemathesis.config._operations import OperationConfig, OperationsConfig
from schemathesis.config._output import OutputConfig
from schemathesis.config._parameters import load_parameters
from schemathesis.config._phases import PhasesConfig
from schemathesis.config._rate_limit import build_limiter
from schemathesis.config._report import ReportsConfig
from schemathesis.config._warnings import WarningsConfig
from schemathesis.core import HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER, NOT_SET, NotSet, hooks
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
    continue_on_failure: bool | None
    tls_verify: bool | str | None
    rate_limit: Limiter | None
    max_redirects: int | None
    request_timeout: float | int | None
    request_cert: str | None
    request_cert_key: str | None
    parameters: dict[str, Any]
    warnings: WarningsConfig
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
        "continue_on_failure",
        "tls_verify",
        "rate_limit",
        "_rate_limit",
        "max_redirects",
        "request_timeout",
        "request_cert",
        "request_cert_key",
        "parameters",
        "warnings",
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
        workers: int | Literal["auto"] = DEFAULT_WORKERS,
        proxy: str | None = None,
        continue_on_failure: bool | None = None,
        tls_verify: bool | str = True,
        rate_limit: str | None = None,
        max_redirects: int | None = None,
        request_timeout: float | int | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        parameters: dict[str, Any] | None = None,
        warnings: WarningsConfig | None = None,
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
        self.continue_on_failure = continue_on_failure
        self.tls_verify = tls_verify
        if rate_limit is not None:
            self.rate_limit = build_limiter(rate_limit)
        else:
            self.rate_limit = rate_limit
        self._rate_limit = rate_limit
        self.max_redirects = max_redirects
        self.request_timeout = request_timeout
        self.request_cert = request_cert
        self.request_cert_key = request_cert_key
        self.parameters = parameters or {}
        self.warnings = warnings or WarningsConfig.from_value(None)
        self.auth = auth or AuthConfig()
        self.checks = checks or ChecksConfig()
        self.phases = phases or PhasesConfig()
        self.generation = generation or GenerationConfig()
        self.operations = operations or OperationsConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        return cls(
            base_url=resolve(data.get("base-url")),
            headers={resolve(key): resolve(value) for key, value in data.get("headers", {}).items()}
            if "headers" in data
            else None,
            hooks_=resolve(data.get("hooks")),
            workers=data.get("workers", DEFAULT_WORKERS),
            proxy=resolve(data.get("proxy")),
            continue_on_failure=data.get("continue-on-failure", None),
            tls_verify=resolve(data.get("tls-verify", True)),
            rate_limit=resolve(data.get("rate-limit")),
            max_redirects=data.get("max-redirects"),
            request_timeout=data.get("request-timeout"),
            request_cert=resolve(data.get("request-cert")),
            request_cert_key=resolve(data.get("request-cert-key")),
            parameters=load_parameters(data),
            auth=AuthConfig.from_dict(data.get("auth", {})),
            warnings=WarningsConfig.from_value(data.get("warnings")),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            phases=PhasesConfig.from_dict(data.get("phases", {})),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            operations=OperationsConfig(
                operations=[OperationConfig.from_dict(operation) for operation in data.get("operations", [])]
            ),
        )

    def update(
        self,
        *,
        base_url: str | None = None,
        headers: dict | None = None,
        basic_auth: tuple[str, str] | None = None,
        workers: int | Literal["auto"] | None = None,
        continue_on_failure: bool | None = None,
        rate_limit: str | None = None,
        max_redirects: int | None = None,
        request_timeout: float | int | None = None,
        tls_verify: bool | str | None = None,
        request_cert: str | None = None,
        request_cert_key: str | None = None,
        parameters: dict[str, Any] | None = None,
        proxy: str | None = None,
        suppress_health_check: list[HealthCheck] | None = None,
        warnings: WarningsConfig | None = None,
    ) -> None:
        if base_url is not None:
            _validate_base_url(base_url)
            self.base_url = base_url

        if headers is not None:
            _headers = self.headers or {}
            _headers.update(headers)
            self.headers = _headers

        if basic_auth is not None:
            self.auth.update(basic=basic_auth)

        if workers is not None:
            if isinstance(workers, int):
                self.workers = workers
            else:
                self.workers = get_workers_count()

        if continue_on_failure is not None:
            self.continue_on_failure = continue_on_failure

        if rate_limit is not None:
            self.rate_limit = build_limiter(rate_limit)

        if max_redirects is not None:
            self.max_redirects = max_redirects

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

        if parameters is not None:
            self.parameters = parameters

        if suppress_health_check is not None:
            self.suppress_health_check = suppress_health_check

        if warnings is not None:
            self.warnings = warnings

    @property
    def config_path(self) -> str | None:
        """Filesystem path to the loaded configuration file, if any.

        Returns None if using default configuration.
        """
        if self._parent is not None:
            return self._parent.config_path
        return None

    def auth_for(self, *, operation: APIOperation | None = None) -> tuple[str, str] | None:
        """Get auth credentials, prioritizing operation-specific configs."""
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.auth.basic is not None:
                return config.auth.basic
        if self.auth.basic is not None:
            return self.auth.basic
        return None

    def headers_for(self, *, operation: APIOperation | None = None) -> dict[str, str]:
        """Get explicitly configured headers."""
        headers = self.headers.copy() if self.headers else {}
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.headers is not None:
                headers.update(config.headers)
        return headers

    def max_redirects_for(self, *, operation: APIOperation | None = None) -> int | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.max_redirects is not None:
                return config.max_redirects
        if self.max_redirects is not None:
            return self.max_redirects
        return None

    def request_timeout_for(self, *, operation: APIOperation | None = None) -> float | int | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.request_timeout is not None:
                return config.request_timeout
        if self.request_timeout is not None:
            return self.request_timeout
        return None

    def tls_verify_for(self, *, operation: APIOperation | None = None) -> bool | str | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.tls_verify is not None:
                return config.tls_verify
        if self.tls_verify is not None:
            return self.tls_verify
        return None

    def request_cert_for(self, *, operation: APIOperation | None = None) -> str | tuple[str, str] | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.request_cert is not None:
                if config.request_cert_key:
                    return (config.request_cert, config.request_cert_key)
                return config.request_cert
        if self.request_cert is not None:
            if self.request_cert_key:
                return (self.request_cert, self.request_cert_key)
            return self.request_cert
        return None

    def proxy_for(self, *, operation: APIOperation | None = None) -> str | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.proxy is not None:
                return config.proxy
        if self.proxy is not None:
            return self.proxy
        return None

    def rate_limit_for(self, *, operation: APIOperation | None = None) -> Limiter | None:
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.rate_limit is not None:
                return config.rate_limit
        if self.rate_limit is not None:
            return self.rate_limit
        return None

    def warnings_for(self, *, operation: APIOperation | None = None) -> WarningsConfig:
        # Operation can be absent on some non-fatal errors due to schema parsing
        if operation is not None:
            config = self.operations.get_for_operation(operation=operation)
            if config.warnings is not None:
                return config.warnings
        return self.warnings

    def phases_for(self, *, operation: APIOperation | None) -> PhasesConfig:
        configs = []
        if operation is not None:
            for op in self.operations.operations:
                if op._filter_set.applies_to(operation=operation):
                    configs.append(op.phases)
        if not configs:
            return self.phases
        configs.append(self.phases)
        return PhasesConfig.from_hierarchy(configs)

    def generation_for(
        self,
        *,
        operation: APIOperation | None = None,
        phase: str | None = None,
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
            phases = self.phases_for(operation=operation)
            phase_config = phases.get_by_name(name=phase)
            if not phase_config._is_default:
                configs.append(phase_config.generation)
        if not configs:
            return self.generation
        configs.append(self.generation)
        return GenerationConfig.from_hierarchy(configs)

    def checks_config_for(
        self,
        *,
        operation: APIOperation | None = None,
        phase: str | None = None,
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
            phases = self.phases_for(operation=operation)
            phase_config = phases.get_by_name(name=phase)
            if not phase_config._is_default:
                configs.append(phase_config.checks)
        if not configs:
            return self.checks
        configs.append(self.checks)
        return ChecksConfig.from_hierarchy(configs)

    def get_hypothesis_settings(
        self,
        *,
        operation: APIOperation | None = None,
        phase: str | None = None,
    ) -> hypothesis.settings:
        import hypothesis
        from hypothesis.database import DirectoryBasedExampleDatabase, InMemoryExampleDatabase

        config = self.generation_for(operation=operation, phase=phase)
        kwargs: dict[str, Any] = {}

        if config.max_examples is not None:
            kwargs["max_examples"] = config.max_examples
        phases = set(hypothesis.Phase) - {hypothesis.Phase.explain}
        if config.no_shrink:
            phases.discard(hypothesis.Phase.shrink)
        database = config.database
        if database is not None:
            if database.lower() == "none":
                kwargs["database"] = None
                phases.discard(hypothesis.Phase.reuse)
            elif database == HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER:
                kwargs["database"] = InMemoryExampleDatabase()
            else:
                kwargs["database"] = DirectoryBasedExampleDatabase(database)

        return hypothesis.settings(
            derandomize=config.deterministic,
            print_blob=False,
            deadline=None,
            verbosity=hypothesis.Verbosity.quiet,
            suppress_health_check=[check for item in self.suppress_health_check for check in item.as_hypothesis()],
            phases=phases,
            # NOTE: Ignoring any operation-specific config as stateful tests are not operation-specific
            stateful_step_count=self.phases.stateful.max_steps,
            **kwargs,
        )

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
    def suppress_health_check(self) -> list[HealthCheck]:
        return self._get_parent().suppress_health_check

    @suppress_health_check.setter
    def suppress_health_check(self, value: list[HealthCheck]) -> None:
        parent = self._get_parent()
        parent.suppress_health_check = value

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

    __slots__ = ("default", "named", "_override")

    def __init__(
        self,
        *,
        default: ProjectConfig | None = None,
        named: dict[str, ProjectConfig] | None = None,
    ) -> None:
        self.default = default or ProjectConfig()
        self.named = named or {}
        self._override: ProjectConfig | NotSet = NOT_SET

    @property
    def override(self) -> ProjectConfig:
        if isinstance(self._override, NotSet):
            self._override = ProjectConfig()
            self._override._parent = self.default._parent
        return self._override

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

    def get_default(self) -> ProjectConfig:
        config = ProjectConfig.from_hierarchy([self.override, self.default])
        config._parent = self.default._parent
        return config

    def get(self, schema: dict[str, Any]) -> ProjectConfig:
        # Highest priority goes to `override`, then config specifically
        # for the given project, then the "default" project config
        configs = []
        if not isinstance(self._override, NotSet):
            configs.append(self._override)
        title = schema.get("info", {}).get("title")
        if title is not None:
            named = self.named.get(title)
            if named is not None:
                configs.append(named)
        if not configs:
            return self.default
        configs.append(self.default)
        config = ProjectConfig.from_hierarchy(configs)
        config._parent = self.default._parent
        return config
