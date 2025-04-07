from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from schemathesis.config._diff_base import DiffBase

NOT_A_SERVER_ERROR_EXPECTED_STATUSES = ["2xx", "3xx", "4xx"]
NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES = ["400", "401", "403", "404", "406", "422", "428", "5xx"]
POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES = ["2xx", "401", "403", "404"]
MISSING_REQUIRED_HEADER_EXPECTED_STATUSES = ["406"]


@dataclass(repr=False)
class SimpleCheckConfig(DiffBase):
    enabled: bool
    _explicit_attrs: set[str]

    __slots__ = ("enabled", "_explicit_attrs")

    def __init__(
        self,
        *,
        enabled: bool = True,
        _explicit_attrs: set[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self._explicit_attrs = _explicit_attrs or set()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimpleCheckConfig:
        return cls(
            enabled=data.get("enabled", True),
            _explicit_attrs=cls.get_explicit_attrs(set(data)),
        )


@dataclass(repr=False)
class CheckConfig(DiffBase):
    enabled: bool
    expected_statuses: list[str]
    _explicit_attrs: set[str]

    __slots__ = ("enabled", "expected_statuses", "_explicit_attrs")

    def __init__(
        self,
        *,
        enabled: bool = True,
        expected_statuses: Sequence[str | int],
        _explicit_attrs: set[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.expected_statuses = [str(status) for status in expected_statuses]
        self._explicit_attrs = _explicit_attrs or set()

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_expected_statuses: list[str]) -> CheckConfig:
        enabled = data.get("enabled", True)
        return cls(
            enabled=enabled,
            expected_statuses=data.get("expected-statuses", default_expected_statuses),
            _explicit_attrs=cls.get_explicit_attrs(set(data)),
        )


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
        self.not_a_server_error = not_a_server_error or CheckConfig(
            expected_statuses=NOT_A_SERVER_ERROR_EXPECTED_STATUSES
        )
        self.status_code_conformance = status_code_conformance or SimpleCheckConfig()
        self.content_type_conformance = content_type_conformance or SimpleCheckConfig()
        self.response_schema_conformance = response_schema_conformance or SimpleCheckConfig()
        self.positive_data_acceptance = positive_data_acceptance or CheckConfig(
            expected_statuses=POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES
        )
        self.negative_data_rejection = negative_data_rejection or CheckConfig(
            expected_statuses=NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES
        )
        self.use_after_free = use_after_free or SimpleCheckConfig()
        self.ensure_resource_availability = ensure_resource_availability or SimpleCheckConfig()
        self.missing_required_header = missing_required_header or CheckConfig(
            expected_statuses=MISSING_REQUIRED_HEADER_EXPECTED_STATUSES
        )
        self.ignored_auth = ignored_auth or SimpleCheckConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChecksConfig:
        # Use the outer "enabled" value as default for all checks.
        default_enabled = data.get("enabled", None)

        def merge(sub: dict[str, Any]) -> dict[str, Any]:
            # Merge the default enabled flag with the sub-dict; the sub-dict takes precedence.
            if default_enabled is not None:
                return {"enabled": default_enabled, **sub}
            return sub

        return cls(
            not_a_server_error=CheckConfig.from_dict(
                merge(data.get("not_a_server_error", {})),
                default_expected_statuses=NOT_A_SERVER_ERROR_EXPECTED_STATUSES,
            ),
            status_code_conformance=SimpleCheckConfig.from_dict(merge(data.get("status_code_conformance", {}))),
            content_type_conformance=SimpleCheckConfig.from_dict(merge(data.get("content_type_conformance", {}))),
            response_schema_conformance=SimpleCheckConfig.from_dict(merge(data.get("response_schema_conformance", {}))),
            positive_data_acceptance=CheckConfig.from_dict(
                merge(data.get("positive_data_acceptance", {})),
                default_expected_statuses=POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES,
            ),
            negative_data_rejection=CheckConfig.from_dict(
                merge(data.get("negative_data_rejection", {})),
                default_expected_statuses=NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES,
            ),
            use_after_free=SimpleCheckConfig.from_dict(merge(data.get("use_after_free", {}))),
            ensure_resource_availability=SimpleCheckConfig.from_dict(
                merge(data.get("ensure_resource_availability", {}))
            ),
            missing_required_header=CheckConfig.from_dict(
                merge(data.get("missing_required_header", {})),
                default_expected_statuses=MISSING_REQUIRED_HEADER_EXPECTED_STATUSES,
            ),
            ignored_auth=SimpleCheckConfig.from_dict(merge(data.get("ignored_auth", {}))),
        )

    @classmethod
    def from_many(cls, configs: list[ChecksConfig]) -> ChecksConfig:
        if not configs:
            return cls()
        # Start with the lowest precedence config.
        merged = configs[-1]
        # Iterate from second-last to first, merging upward.
        for cfg in reversed(configs[:-1]):
            merged = cls(
                not_a_server_error=cfg.not_a_server_error.merge(merged.not_a_server_error),
                status_code_conformance=cfg.status_code_conformance.merge(merged.status_code_conformance),
                content_type_conformance=cfg.content_type_conformance.merge(merged.content_type_conformance),
                response_schema_conformance=cfg.response_schema_conformance.merge(merged.response_schema_conformance),
                positive_data_acceptance=cfg.positive_data_acceptance.merge(merged.positive_data_acceptance),
                negative_data_rejection=cfg.negative_data_rejection.merge(merged.negative_data_rejection),
                use_after_free=cfg.use_after_free.merge(merged.use_after_free),
                ensure_resource_availability=cfg.ensure_resource_availability.merge(
                    merged.ensure_resource_availability
                ),
                missing_required_header=cfg.missing_required_header.merge(merged.missing_required_header),
                ignored_auth=cfg.ignored_auth.merge(merged.ignored_auth),
            )
        return merged
