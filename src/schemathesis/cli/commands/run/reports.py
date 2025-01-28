from __future__ import annotations

from enum import Enum
from pathlib import Path

from click.utils import LazyFile

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


class ReportConfig:
    """Configuration for test report generation."""

    __slots__ = (
        "formats",
        "directory",
        "junit_path",
        "vcr_path",
        "har_path",
        "preserve_bytes",
        "sanitize_output",
    )

    def __init__(
        self,
        formats: list[ReportFormat] | None = None,
        directory: Path = DEFAULT_REPORT_DIRECTORY,
        *,
        junit_path: LazyFile | None = None,
        vcr_path: LazyFile | None = None,
        har_path: LazyFile | None = None,
        preserve_bytes: bool = False,
        sanitize_output: bool = True,
    ) -> None:
        self.formats = formats or []
        # Auto-enable formats when paths are specified
        if junit_path and ReportFormat.JUNIT not in self.formats:
            self.formats.append(ReportFormat.JUNIT)
        if vcr_path and ReportFormat.VCR not in self.formats:
            self.formats.append(ReportFormat.VCR)
        if har_path and ReportFormat.HAR not in self.formats:
            self.formats.append(ReportFormat.HAR)
        self.directory = directory
        self.junit_path = junit_path
        self.vcr_path = vcr_path
        self.har_path = har_path
        self.preserve_bytes = preserve_bytes
        self.sanitize_output = sanitize_output

    def get_path(self, format: ReportFormat) -> LazyFile:
        """Get the final path for a specific format."""
        custom_path = getattr(self, f"{format.value}_path")
        if custom_path is not None:
            return custom_path
        return LazyFile(self.directory / f"{format.value}.{format.extension}", mode="w", encoding="utf-8")
