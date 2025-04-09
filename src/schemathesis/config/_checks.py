from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Sequence

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
    _DEFAULT_EXPECTED_STATUSES: ClassVar[list[str]]

    __slots__ = ("enabled", "expected_statuses", "_explicit_attrs")

    def __init__(
        self,
        *,
        enabled: bool = True,
        expected_statuses: Sequence[str | int] | None = None,
        _explicit_attrs: set[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.expected_statuses = (
            [str(status) for status in expected_statuses]
            if expected_statuses is not None
            else self._DEFAULT_EXPECTED_STATUSES
        )
        self._explicit_attrs = _explicit_attrs or set()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckConfig:
        enabled = data.get("enabled", True)
        return cls(
            enabled=enabled,
            expected_statuses=data.get("expected-statuses", cls._DEFAULT_EXPECTED_STATUSES),
            _explicit_attrs=cls.get_explicit_attrs(set(data)),
        )


class NotAServerErrorConfig(CheckConfig):
    _DEFAULT_EXPECTED_STATUSES = NOT_A_SERVER_ERROR_EXPECTED_STATUSES


class PositiveDataAcceptanceConfig(CheckConfig):
    _DEFAULT_EXPECTED_STATUSES = POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES


class NegativeDataRejectionConfig(CheckConfig):
    _DEFAULT_EXPECTED_STATUSES = NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES


class MissingRequiredHeaderConfig(CheckConfig):
    _DEFAULT_EXPECTED_STATUSES = MISSING_REQUIRED_HEADER_EXPECTED_STATUSES


@dataclass(repr=False)
class ChecksConfig(DiffBase):
    not_a_server_error: NotAServerErrorConfig
    status_code_conformance: SimpleCheckConfig
    content_type_conformance: SimpleCheckConfig
    response_schema_conformance: SimpleCheckConfig
    response_headers_conformance: SimpleCheckConfig
    positive_data_acceptance: PositiveDataAcceptanceConfig
    negative_data_rejection: NegativeDataRejectionConfig
    use_after_free: SimpleCheckConfig
    ensure_resource_availability: SimpleCheckConfig
    missing_required_header: MissingRequiredHeaderConfig
    ignored_auth: SimpleCheckConfig

    __slots__ = (
        "not_a_server_error",
        "status_code_conformance",
        "content_type_conformance",
        "response_schema_conformance",
        "response_headers_conformance",
        "positive_data_acceptance",
        "negative_data_rejection",
        "use_after_free",
        "ensure_resource_availability",
        "missing_required_header",
        "ignored_auth",
        "_unknown_included",
        "_unknown_excluded",
    )

    def __init__(
        self,
        *,
        not_a_server_error: NotAServerErrorConfig | None = None,
        status_code_conformance: SimpleCheckConfig | None = None,
        content_type_conformance: SimpleCheckConfig | None = None,
        response_schema_conformance: SimpleCheckConfig | None = None,
        response_headers_conformance: SimpleCheckConfig | None = None,
        positive_data_acceptance: PositiveDataAcceptanceConfig | None = None,
        negative_data_rejection: NegativeDataRejectionConfig | None = None,
        use_after_free: SimpleCheckConfig | None = None,
        ensure_resource_availability: SimpleCheckConfig | None = None,
        missing_required_header: MissingRequiredHeaderConfig | None = None,
        ignored_auth: SimpleCheckConfig | None = None,
    ) -> None:
        self.not_a_server_error = not_a_server_error or NotAServerErrorConfig()
        self.status_code_conformance = status_code_conformance or SimpleCheckConfig()
        self.content_type_conformance = content_type_conformance or SimpleCheckConfig()
        self.response_schema_conformance = response_schema_conformance or SimpleCheckConfig()
        self.response_headers_conformance = response_headers_conformance or SimpleCheckConfig()
        self.positive_data_acceptance = positive_data_acceptance or PositiveDataAcceptanceConfig()
        self.negative_data_rejection = negative_data_rejection or NegativeDataRejectionConfig()
        self.use_after_free = use_after_free or SimpleCheckConfig()
        self.ensure_resource_availability = ensure_resource_availability or SimpleCheckConfig()
        self.missing_required_header = missing_required_header or MissingRequiredHeaderConfig()
        self.ignored_auth = ignored_auth or SimpleCheckConfig()
        self._unknown_included: set[str] = set()
        self._unknown_excluded: set[str] = set()

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
            not_a_server_error=NotAServerErrorConfig.from_dict(
                merge(data.get("not_a_server_error", {})),
            ),
            status_code_conformance=SimpleCheckConfig.from_dict(merge(data.get("status_code_conformance", {}))),
            content_type_conformance=SimpleCheckConfig.from_dict(merge(data.get("content_type_conformance", {}))),
            response_schema_conformance=SimpleCheckConfig.from_dict(merge(data.get("response_schema_conformance", {}))),
            response_headers_conformance=SimpleCheckConfig.from_dict(
                merge(data.get("response_headers_conformance", {}))
            ),
            positive_data_acceptance=PositiveDataAcceptanceConfig.from_dict(
                merge(data.get("positive_data_acceptance", {})),
            ),
            negative_data_rejection=NegativeDataRejectionConfig.from_dict(
                merge(data.get("negative_data_rejection", {})),
            ),
            use_after_free=SimpleCheckConfig.from_dict(merge(data.get("use_after_free", {}))),
            ensure_resource_availability=SimpleCheckConfig.from_dict(
                merge(data.get("ensure_resource_availability", {}))
            ),
            missing_required_header=MissingRequiredHeaderConfig.from_dict(
                merge(data.get("missing_required_header", {})),
            ),
            ignored_auth=SimpleCheckConfig.from_dict(merge(data.get("ignored_auth", {}))),
        )

    def get_by_name(self, *, name: str) -> CheckConfig | SimpleCheckConfig:
        try:
            return getattr(self, name)
        except AttributeError:
            enabled = True
            if name in self._unknown_excluded:
                enabled = False
            return SimpleCheckConfig(enabled=enabled, _explicit_attrs={"enabled"})

    def override(
        self, *, included_check_names: list[str] | None = None, excluded_check_names: list[str] | None = None
    ) -> None:
        known_names = {name for name in self.__slots__ if not name.startswith("_")}
        for name in known_names:
            # Check in explicitly excluded or not in explicitly included
            if name in (excluded_check_names or []) or (
                included_check_names is not None and name not in included_check_names
            ):
                config = self.get_by_name(name=name)
                config.enabled = False
                config._explicit_attrs.add("enabled")
            elif included_check_names is not None and name in included_check_names:
                config = self.get_by_name(name=name)
                config.enabled = True
                config._explicit_attrs.add("enabled")

        self._unknown_included.update(
            name for name in (included_check_names or []) if name not in known_names and name != "all"
        )
        self._unknown_excluded.update(name for name in (excluded_check_names or []) if name not in known_names)

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
                response_headers_conformance=cfg.response_headers_conformance.merge(
                    merged.response_headers_conformance
                ),
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
