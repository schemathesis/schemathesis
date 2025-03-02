from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path

import tomli

from schemathesis.config._checks import CheckConfig, ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._error import ConfigError
from schemathesis.config._generation import GenerationConfig
from schemathesis.config._health_check import HealthCheck
from schemathesis.config._output import OutputConfig
from schemathesis.config._parameters import ParameterOverride
from schemathesis.config._phases import CoveragePhaseConfig, PhaseConfig, PhasesConfig
from schemathesis.config._projects import ProjectConfig, ProjectsConfig
from schemathesis.config._report import DEFAULT_REPORT_DIRECTORY, ReportConfig, ReportFormat, ReportsConfig

__all__ = [
    "SchemathesisConfig",
    "ConfigError",
    "HealthCheck",
    "ReportConfig",
    "ReportsConfig",
    "ReportFormat",
    "DEFAULT_REPORT_DIRECTORY",
    "ParameterOverride",
    "GenerationConfig",
    "OutputConfig",
    "ChecksConfig",
    "CheckConfig",
    "PhaseConfig",
    "PhasesConfig",
    "CoveragePhaseConfig",
    "ProjectsConfig",
    "ProjectConfig",
]


@dataclass(repr=False)
class SchemathesisConfig(DiffBase):
    color: bool | None
    suppress_health_check: list[HealthCheck]
    max_failures: int | None
    reports: ReportsConfig
    output: OutputConfig
    projects: ProjectsConfig

    __slots__ = ("color", "suppress_health_check", "max_failures", "reports", "output", "projects")

    def __init__(
        self,
        *,
        color: bool | None = None,
        suppress_health_check: list[HealthCheck] | None = None,
        max_failures: int | None = None,
        reports: ReportsConfig | None = None,
        output: OutputConfig | None = None,
        projects: ProjectsConfig | None = None,
    ):
        self.color = color
        self.suppress_health_check = suppress_health_check or []
        self.max_failures = max_failures
        self.reports = reports or ReportsConfig()
        self.output = output or OutputConfig()
        self.projects = projects or ProjectsConfig()

    @classmethod
    def discover(cls) -> SchemathesisConfig:
        """Discover the Schemathesis configuration file.

        Search for 'schemathesis.toml' in the current directory and then in each parent directory,
        stopping when a directory containing a '.git' folder is encountered or the filesystem root is reached.
        If a config file is found, load it; otherwise, return a default configuration.
        """
        current_dir = Path.cwd()
        config_file = None

        while True:
            candidate = current_dir / "schemathesis.toml"
            if candidate.exists():
                config_file = candidate
                break

            # Stop searching if we've reached a git repository root
            if (current_dir / ".git").exists():
                break

            # Stop if we've reached the filesystem root
            if current_dir.parent == current_dir:
                break

            current_dir = current_dir.parent

        if config_file:
            return cls.from_path(config_file)
        return cls()

    def override(
        self, *, color: bool | None, suppress_health_check: list[HealthCheck] | None, max_failures: int | None
    ) -> None:
        if color is not None:
            self.color = color
        if suppress_health_check is not None:
            self.suppress_health_check = suppress_health_check
        if max_failures is not None:
            self.max_failures = max_failures

    @classmethod
    def from_path(cls, path: PathLike | str) -> SchemathesisConfig:
        """Load configuration from a file path."""
        with open(path) as fd:
            return cls.from_str(fd.read())

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
            max_failures=data.get("max-failures"),
            reports=ReportsConfig.from_dict(data.get("reports", {})),
            output=OutputConfig.from_dict(data.get("output", {})),
            projects=ProjectsConfig.from_dict(data),
        )
