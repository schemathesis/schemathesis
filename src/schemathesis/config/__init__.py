from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from random import Random

from schemathesis.config._auth import ApiKeyAuthConfig, HttpBasicAuthConfig, HttpBearerAuthConfig
from schemathesis.config._checks import (
    CheckConfig,
    ChecksConfig,
    NotAServerErrorConfig,
    PositiveDataAcceptanceConfig,
    SimpleCheckConfig,
)
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._error import ConfigError
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._health_check import HealthCheck
from schemathesis.config._output import OutputConfig, SanitizationConfig, TruncationConfig
from schemathesis.config._phases import (
    CoveragePhaseConfig,
    ExamplesPhaseConfig,
    ExtraDataSourcesConfig,
    FuzzingPhaseConfig,
    InferenceAlgorithm,
    OperationOrdering,
    PhasesConfig,
    StatefulPhaseConfig,
)
from schemathesis.config._projects import ProjectConfig, ProjectsConfig, get_workers_count
from schemathesis.config._report import DEFAULT_REPORT_DIRECTORY, ReportConfig, ReportFormat, ReportsConfig
from schemathesis.config._warnings import SchemathesisWarning, WarningsConfig

if sys.version_info < (3, 11):
    import tomli
else:
    import tomllib as tomli

__all__ = [
    "SchemathesisConfig",
    "ConfigError",
    "HealthCheck",
    "ReportConfig",
    "ReportsConfig",
    "ReportFormat",
    "DEFAULT_REPORT_DIRECTORY",
    "GenerationConfig",
    "OutputConfig",
    "SanitizationConfig",
    "TruncationConfig",
    "ChecksConfig",
    "CheckConfig",
    "NotAServerErrorConfig",
    "PositiveDataAcceptanceConfig",
    "SimpleCheckConfig",
    "PhasesConfig",
    "FuzzingPhaseConfig",
    "CoveragePhaseConfig",
    "ExamplesPhaseConfig",
    "StatefulPhaseConfig",
    "ExtraDataSourcesConfig",
    "InferenceAlgorithm",
    "OperationOrdering",
    "ProjectsConfig",
    "ProjectConfig",
    "get_workers_count",
    "SchemathesisWarning",
    "WarningsConfig",
    "ApiKeyAuthConfig",
    "HttpBasicAuthConfig",
    "HttpBearerAuthConfig",
]


@dataclass(repr=False)
class SchemathesisConfig(DiffBase):
    color: bool | None
    suppress_health_check: list[HealthCheck]
    _seed: int | None
    _config_path: str | None
    wait_for_schema: float | int | None
    max_failures: int | None
    reports: ReportsConfig
    output: OutputConfig
    projects: ProjectsConfig

    __slots__ = (
        "color",
        "suppress_health_check",
        "_seed",
        "_config_path",
        "wait_for_schema",
        "max_failures",
        "reports",
        "output",
        "projects",
    )

    def __init__(
        self,
        *,
        color: bool | None = None,
        suppress_health_check: list[HealthCheck] | None = None,
        seed: int | None = None,
        wait_for_schema: float | int | None = None,
        max_failures: int | None = None,
        reports: ReportsConfig | None = None,
        output: OutputConfig | None = None,
        projects: ProjectsConfig | None = None,
    ):
        self.color = color
        self.suppress_health_check = suppress_health_check or []
        self._seed = seed
        self._config_path = None
        self.wait_for_schema = wait_for_schema
        self.max_failures = max_failures
        self.reports = reports or ReportsConfig()
        self.output = output or OutputConfig()
        self.projects = projects or ProjectsConfig()
        self.projects._set_parent(self)

    @property
    def seed(self) -> int:
        if self._seed is None:
            self._seed = Random().getrandbits(128)
        return self._seed

    @property
    def config_path(self) -> str | None:
        """Filesystem path to the loaded configuration file, if any.

        Returns None if using default configuration.
        """
        return self._config_path

    @classmethod
    def discover(cls) -> SchemathesisConfig:
        """Discover the Schemathesis configuration file.

        Search for 'schemathesis.toml' in the current directory and then in each parent directory,
        stopping when a directory containing a '.git' folder is encountered or the filesystem root is reached.
        If a config file is found, load it; otherwise, return a default configuration.
        """
        current_dir = os.getcwd()
        config_file = None

        while True:
            candidate = os.path.join(current_dir, "schemathesis.toml")
            if os.path.isfile(candidate):
                config_file = candidate
                break

            # Stop searching if we've reached a git repository root
            git_dir = os.path.join(current_dir, ".git")
            if os.path.isdir(git_dir):
                break

            # Stop if we've reached the filesystem root
            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            current_dir = parent

        if config_file:
            return cls.from_path(config_file)
        return cls()

    def update(
        self,
        *,
        color: bool | None = None,
        suppress_health_check: list[HealthCheck] | None = None,
        seed: int | None = None,
        wait_for_schema: float | int | None = None,
        max_failures: int | None,
    ) -> None:
        """Set top-level configuration options."""
        if color is not None:
            self.color = color
        if suppress_health_check is not None:
            self.suppress_health_check = suppress_health_check
        if seed is not None:
            self._seed = seed
        if wait_for_schema is not None:
            self.wait_for_schema = wait_for_schema
        if max_failures is not None:
            self.max_failures = max_failures

    @classmethod
    def from_path(cls, path: PathLike | str) -> SchemathesisConfig:
        """Load configuration from a file path."""
        with open(path, encoding="utf-8") as fd:
            config = cls.from_str(fd.read())
            config._config_path = str(Path(path).resolve())
            return config

    @classmethod
    def from_str(cls, data: str) -> SchemathesisConfig:
        """Parse configuration from a string."""
        parsed = tomli.loads(data)
        return cls.from_dict(parsed)

    @classmethod
    def from_dict(cls, data: dict) -> SchemathesisConfig:
        """Create a config instance from a dictionary."""
        from jsonschema.exceptions import ValidationError

        from schemathesis.config._validator import CONFIG_VALIDATOR

        try:
            CONFIG_VALIDATOR.validate(data)
        except ValidationError as exc:
            raise ConfigError.from_validation_error(exc) from None
        return cls(
            color=data.get("color"),
            suppress_health_check=[HealthCheck(name) for name in data.get("suppress-health-check", [])],
            seed=data.get("seed"),
            wait_for_schema=data.get("wait-for-schema"),
            max_failures=data.get("max-failures"),
            reports=ReportsConfig.from_dict(data.get("reports", {})),
            output=OutputConfig.from_dict(data.get("output", {})),
            projects=ProjectsConfig.from_dict(data),
        )
