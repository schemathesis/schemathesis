from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve

DEFAULT_REPORT_DIRECTORY = Path("./schemathesis-report")


class ReportFormat(str, Enum):
    """Available report formats."""

    JUNIT = "junit"
    VCR = "vcr"
    HAR = "har"

    @property
    def extension(self) -> str:
        """File extension for this format."""
        return {
            self.JUNIT: "xml",
            self.VCR: "yaml",
            self.HAR: "json",
        }[self]


@dataclass(repr=False)
class ReportConfig(DiffBase):
    enabled: bool
    path: Path | None

    __slots__ = ("enabled", "path")

    def __init__(self, *, enabled: bool = False, path: Path | None = None) -> None:
        self.enabled = enabled
        self.path = path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportConfig:
        path = resolve(data.get("path"))
        if path is not None:
            return cls(enabled=True, path=Path(path))
        enabled = data.get("enabled", False)
        return cls(enabled=enabled, path=path)


@dataclass(repr=False)
class ReportsConfig(DiffBase):
    directory: Path
    preserve_bytes: bool
    junit: ReportConfig
    vcr: ReportConfig
    har: ReportConfig
    _timestamp: str

    __slots__ = ("directory", "preserve_bytes", "junit", "vcr", "har", "_timestamp")

    def __init__(
        self,
        *,
        directory: str | None = None,
        preserve_bytes: bool = False,
        junit: ReportConfig | None = None,
        vcr: ReportConfig | None = None,
        har: ReportConfig | None = None,
    ) -> None:
        self.directory = Path(resolve(directory) or DEFAULT_REPORT_DIRECTORY)
        self.preserve_bytes = preserve_bytes
        self.junit = junit or ReportConfig()
        self.vcr = vcr or ReportConfig()
        self.har = har or ReportConfig()
        self._timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportsConfig:
        return cls(
            directory=data.get("directory"),
            preserve_bytes=data.get("preserve-bytes", False),
            junit=ReportConfig.from_dict(data.get("junit", {})),
            vcr=ReportConfig.from_dict(data.get("vcr", {})),
            har=ReportConfig.from_dict(data.get("har", {})),
        )

    def update(
        self,
        *,
        formats: list[ReportFormat] | None = None,
        junit_path: str | None = None,
        vcr_path: str | None = None,
        har_path: str | None = None,
        directory: Path = DEFAULT_REPORT_DIRECTORY,
        preserve_bytes: bool | None = None,
    ) -> None:
        formats = formats or []
        if junit_path is not None or ReportFormat.JUNIT in formats:
            self.junit.enabled = True
            self.junit.path = Path(junit_path) if junit_path is not None else junit_path
        if vcr_path is not None or ReportFormat.VCR in formats:
            self.vcr.enabled = True
            self.vcr.path = Path(vcr_path) if vcr_path is not None else vcr_path
        if har_path is not None or ReportFormat.HAR in formats:
            self.har.enabled = True
            self.har.path = Path(har_path) if har_path is not None else har_path
        if directory != DEFAULT_REPORT_DIRECTORY:
            self.directory = directory
        if preserve_bytes is True:
            self.preserve_bytes = preserve_bytes

    def get_path(self, format: ReportFormat) -> Path:
        """Get the final path for a specific format."""
        report: ReportConfig = getattr(self, format.value)
        if report.path is not None:
            return report.path

        return self.directory / f"{format.value}-{self._timestamp}.{format.extension}"
