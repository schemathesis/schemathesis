from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase


@dataclass(repr=False)
class SimpleCheckConfig(DiffBase):
    enabled: bool

    __slots__ = ("enabled",)

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimpleCheckConfig:
        return cls(enabled=data.get("enabled", True))


@dataclass(repr=False)
class CheckConfig(DiffBase):
    enabled: bool
    expected_statuses: list[int] | None

    __slots__ = ("enabled", "expected_statuses")

    def __init__(self, *, enabled: bool = True, expected_statuses: list[int] | None = None) -> None:
        self.enabled = enabled
        self.expected_statuses = expected_statuses

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckConfig:
        enabled = data.get("enabled", True)
        return cls(enabled=enabled, expected_statuses=data.get("expected-statuses"))


@dataclass(repr=False)
class ChecksConfig(DiffBase):
    not_a_server_error: CheckConfig
    status_code_conformance: SimpleCheckConfig
    content_type_conformance: SimpleCheckConfig
    response_schema_conformance: SimpleCheckConfig
    positive_data_acceptance: CheckConfig
    negative_data_rejection: CheckConfig
    use_after_free: SimpleCheckConfig
    ensure_resource_availability: SimpleCheckConfig
    missing_required_header: CheckConfig
    ignored_auth: SimpleCheckConfig

    __slots__ = (
        "not_a_server_error",
        "status_code_conformance",
        "content_type_conformance",
        "response_schema_conformance",
        "positive_data_acceptance",
        "negative_data_rejection",
        "use_after_free",
        "ensure_resource_availability",
        "missing_required_header",
        "ignored_auth",
    )

    def __init__(
        self,
        *,
        not_a_server_error: CheckConfig | None = None,
        status_code_conformance: SimpleCheckConfig | None = None,
        content_type_conformance: SimpleCheckConfig | None = None,
        response_schema_conformance: SimpleCheckConfig | None = None,
        positive_data_acceptance: CheckConfig | None = None,
        negative_data_rejection: CheckConfig | None = None,
        use_after_free: SimpleCheckConfig | None = None,
        ensure_resource_availability: SimpleCheckConfig | None = None,
        missing_required_header: CheckConfig | None = None,
        ignored_auth: SimpleCheckConfig | None = None,
    ) -> None:
        self.not_a_server_error = not_a_server_error or CheckConfig()
        self.status_code_conformance = status_code_conformance or SimpleCheckConfig()
        self.content_type_conformance = content_type_conformance or SimpleCheckConfig()
        self.response_schema_conformance = response_schema_conformance or SimpleCheckConfig()
        self.positive_data_acceptance = positive_data_acceptance or CheckConfig()
        self.negative_data_rejection = negative_data_rejection or CheckConfig()
        self.use_after_free = use_after_free or SimpleCheckConfig()
        self.ensure_resource_availability = ensure_resource_availability or SimpleCheckConfig()
        self.missing_required_header = missing_required_header or CheckConfig()
        self.ignored_auth = ignored_auth or SimpleCheckConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChecksConfig:
        # Use the outer "enabled" value as default for all checks.
        default_enabled = data.get("enabled", True)

        def merge(sub: dict[str, Any]) -> dict[str, Any]:
            # Merge the default enabled flag with the sub-dict; the sub-dict takes precedence.
            merged = {"enabled": default_enabled}
            merged.update(sub)
            return merged

        return cls(
            not_a_server_error=CheckConfig.from_dict(merge(data.get("not_a_server_error", {}))),
            status_code_conformance=SimpleCheckConfig.from_dict(merge(data.get("status_code_conformance", {}))),
            content_type_conformance=SimpleCheckConfig.from_dict(merge(data.get("content_type_conformance", {}))),
            response_schema_conformance=SimpleCheckConfig.from_dict(merge(data.get("response_schema_conformance", {}))),
            positive_data_acceptance=CheckConfig.from_dict(merge(data.get("positive_data_acceptance", {}))),
            negative_data_rejection=CheckConfig.from_dict(merge(data.get("negative_data_rejection", {}))),
            use_after_free=SimpleCheckConfig.from_dict(merge(data.get("use_after_free", {}))),
            ensure_resource_availability=SimpleCheckConfig.from_dict(
                merge(data.get("ensure_resource_availability", {}))
            ),
            missing_required_header=CheckConfig.from_dict(merge(data.get("missing_required_header", {}))),
            ignored_auth=SimpleCheckConfig.from_dict(merge(data.get("ignored_auth", {}))),
        )
