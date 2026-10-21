from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._env import resolve

DEFAULT_REPORT_DIRECTORY = Path("./schemathesis-report")


class ReportFormat(StrEnum):
    """Available report formats."""

    JUNIT = "junit"
    VCR = "vcr"
    HAR = "har"
    NDJSON = "ndjson"
    JSON = "json"
    WFC = "wfc"
    PYTEST = "pytest"
    ALLURE = "allure"
    HTML = "html"

    @property
    def display_name(self) -> str:
        return {
            self.JUNIT: "JUnit",
            self.VCR: "VCR",
            self.HAR: "HAR",
            self.NDJSON: "NDJSON",
            self.JSON: "JSON",
            self.WFC: "WFC",
            self.PYTEST: "pytest",
            self.ALLURE: "Allure",
            self.HTML: "HTML",
        }[self]

    @property
    def extension(self) -> str:
        """File extension for this format."""
        return {
            self.JUNIT: "xml",
            self.VCR: "yaml",
            self.HAR: "json",
            self.NDJSON: "ndjson",
            self.JSON: "json",
            self.WFC: "json",
            self.PYTEST: "py",
            # directory output — no file extension
            self.ALLURE: "",
            self.HTML: "",
        }[self]

    @property
    def is_directory(self) -> bool:
        return not self.extension


@dataclass(repr=False, slots=True)
class ReportConfig(DiffBase):
    enabled: bool
    path: Path | None

    def __init__(self, *, enabled: bool = False, path: Path | None = None) -> None:
        self.enabled = enabled
        self.path = path

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportConfig:
        path = resolve(data.get("path"))
        if path is not None:
            return cls(enabled=True, path=Path(path))._mark_source_keys(data)
        enabled = data.get("enabled", False)
        return cls(enabled=enabled, path=path)._mark_source_keys(data)


@dataclass(repr=False, slots=True)
class ReportsConfig(DiffBase):
    directory: Path
    preserve_bytes: bool
    junit: ReportConfig
    vcr: ReportConfig
    har: ReportConfig
    ndjson: ReportConfig
    json: ReportConfig
    wfc: ReportConfig
    pytest: ReportConfig
    allure: ReportConfig
    html: ReportConfig
    _timestamp: str

    def __init__(
        self,
        *,
        directory: str | None = None,
        preserve_bytes: bool = False,
        junit: ReportConfig | None = None,
        vcr: ReportConfig | None = None,
        har: ReportConfig | None = None,
        ndjson: ReportConfig | None = None,
        json: ReportConfig | None = None,
        wfc: ReportConfig | None = None,
        pytest: ReportConfig | None = None,
        allure: ReportConfig | None = None,
        html: ReportConfig | None = None,
    ) -> None:
        self.directory = Path(resolve(directory) or DEFAULT_REPORT_DIRECTORY)
        self.preserve_bytes = preserve_bytes
        self.junit = junit or ReportConfig()
        self.vcr = vcr or ReportConfig()
        self.har = har or ReportConfig()
        self.ndjson = ndjson or ReportConfig()
        self.json = json or ReportConfig()
        self.wfc = wfc or ReportConfig()
        self.pytest = pytest or ReportConfig()
        self.allure = allure or ReportConfig()
        self.html = html or ReportConfig()
        self._timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportsConfig:
        return cls(
            directory=data.get("directory"),
            preserve_bytes=data.get("preserve-bytes", False),
            junit=ReportConfig.from_dict(data.get("junit", {})),
            vcr=ReportConfig.from_dict(data.get("vcr", {})),
            har=ReportConfig.from_dict(data.get("har", {})),
            ndjson=ReportConfig.from_dict(data.get("ndjson", {})),
            json=ReportConfig.from_dict(data.get("json", {})),
            wfc=ReportConfig.from_dict(data.get("wfc", {})),
            pytest=ReportConfig.from_dict(data.get("pytest", {})),
            allure=ReportConfig.from_dict(data.get("allure", {})),
            html=ReportConfig.from_dict(data.get("html", {})),
        )._mark_source_keys(data)

    def update(
        self,
        *,
        formats: list[ReportFormat] | None = None,
        junit_path: str | None = None,
        vcr_path: str | None = None,
        har_path: str | None = None,
        ndjson_path: str | None = None,
        json_path: str | None = None,
        wfc_path: str | None = None,
        pytest_path: str | None = None,
        allure_path: str | None = None,
        html_path: str | None = None,
        directory: Path = DEFAULT_REPORT_DIRECTORY,
        preserve_bytes: bool | None = None,
    ) -> None:
        formats = formats or []
        paths = {
            ReportFormat.JUNIT: junit_path,
            ReportFormat.VCR: vcr_path,
            ReportFormat.HAR: har_path,
            ReportFormat.NDJSON: ndjson_path,
            ReportFormat.JSON: json_path,
            ReportFormat.WFC: wfc_path,
            ReportFormat.PYTEST: pytest_path,
            ReportFormat.ALLURE: allure_path,
            ReportFormat.HTML: html_path,
        }
        for report_format, path in paths.items():
            if path is not None or report_format in formats:
                report = self.get(report_format)
                report.enabled = True
                report.path = Path(path) if path is not None else None
        if directory != DEFAULT_REPORT_DIRECTORY:
            self.directory = directory
        if preserve_bytes:
            self.preserve_bytes = preserve_bytes

    def get(self, format: ReportFormat) -> ReportConfig:
        """The configuration of one report format."""
        return getattr(self, format.value)

    def get_path(self, format: ReportFormat) -> Path:
        """Get the final path for a specific format."""
        report = self.get(format)
        if report.path is not None:
            return report.path

        if format.is_directory:
            return self.directory / f"{format.value}-{self._timestamp}"
        if format == ReportFormat.PYTEST:
            # pytest collects only `test_*.py` files, and a module name can't contain a hyphen.
            return self.directory / f"test_schemathesis_{self._timestamp}.py"

        return self.directory / f"{format.value}-{self._timestamp}.{format.extension}"

    def get_stable_path(self, format: ReportFormat, suffix: str | None = None) -> Path:
        """Get a stable, timestamp-free path for cross-process transport.

        Unlike get_path(), this omits the timestamp so all workers in an xdist
        run produce identical paths regardless of when they start.
        If `suffix` is given it is inserted before the extension (e.g. ``cassette-abc12345.yaml``).
        """
        report = self.get(format)
        if report.path is not None:
            return report.path
        if format.is_directory:
            name = f"{format.value}-{suffix}" if suffix else format.value
            return self.directory / name
        path = self.directory / f"{format.value}.{format.extension}"
        if suffix:
            path = path.with_stem(f"{path.stem}-{suffix}")
        return path
