from __future__ import annotations

from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from schemathesis.config import WarningsConfig

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig, ReportsConfig

_FILTER_OPTIONS = (
    "include_path",
    "include_method",
    "include_name",
    "include_tag",
    "include_operation_id",
    "include_path_regex",
    "include_method_regex",
    "include_name_regex",
    "include_tag_regex",
    "include_operation_id_regex",
    "exclude_path",
    "exclude_method",
    "exclude_name",
    "exclude_tag",
    "exclude_operation_id",
    "exclude_path_regex",
    "exclude_method_regex",
    "exclude_name_regex",
    "exclude_tag_regex",
    "exclude_operation_id_regex",
    "include_by",
    "exclude_by",
    "exclude_deprecated",
)


def build_filter_set(params: dict[str, Any]) -> dict[str, Any]:
    """Collect the operation filters passed on the command line."""
    return {name: params[name] for name in _FILTER_OPTIONS}


def _file_name(file: IO[str] | None) -> str | None:
    return file.name if file else None


def apply_report_options(reports: ReportsConfig, params: dict[str, Any]) -> None:
    """Override the report configuration with the `--report-*` options."""
    reports.update(
        formats=params["report_formats"],
        junit_path=_file_name(params["report_junit_path"]),
        vcr_path=_file_name(params["report_vcr_path"]),
        har_path=_file_name(params["report_har_path"]),
        ndjson_path=_file_name(params["report_ndjson_path"]),
        json_path=_file_name(params["report_json_path"]),
        wfc_path=_file_name(params["report_wfc_path"]),
        pytest_path=_file_name(params["report_pytest_path"]),
        allure_path=params["report_allure_path"],
        html_path=params["report_html_path"],
        directory=Path(params["report_directory"]),
        preserve_bytes=params["report_preserve_bytes"],
    )


def apply_project_options(
    override: ProjectConfig, params: dict[str, Any], *, baseline: str | None = None, origin: str | None = None
) -> None:
    """Override every project's configuration with the connection, auth and request options."""
    warnings = params["warnings"]
    override.update(
        base_url=params["base_url"],
        baseline=baseline,
        origin=origin,
        headers=params["headers"] or None,
        basic_auth=params["auth"],
        wfc_auth=params["auth_wfc"],
        wfc_user=params["auth_wfc_user"],
        workers=params["workers"],
        continue_on_failure=params["continue_on_failure"],
        rate_limit=params["rate_limit"],
        max_redirects=params["max_redirects"],
        request_timeout=params["request_timeout"],
        request_retries=params["request_retries"],
        tls_verify=params["request_tls_verify"],
        request_cert=params["request_cert"],
        request_cert_key=params["request_cert_key"],
        proxy=params["request_proxy"],
        warnings=WarningsConfig.from_value([w.value for w in warnings] if isinstance(warnings, list) else warnings)
        if warnings is not None
        else None,
    )
