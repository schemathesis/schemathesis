from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from schemathesis.config._diff_base import DiffBase
from schemathesis.config._error import ConfigError

if TYPE_CHECKING:
    from typing_extensions import Self

NOT_A_SERVER_ERROR_EXPECTED_STATUSES = ["2xx", "3xx", "4xx"]
NEGATIVE_DATA_REJECTION_EXPECTED_STATUSES = ["400", "401", "403", "404", "406", "422", "428", "5xx"]
POSITIVE_DATA_ACCEPTANCE_EXPECTED_STATUSES = ["2xx", "401", "403", "404", "409", "5xx"]
MISSING_REQUIRED_HEADER_EXPECTED_STATUSES = ["406"]


def validate_status_codes(value: Sequence[str] | None) -> Sequence[str] | None:
    if not value:
        return value

    invalid = []

    for code in value:
        if len(code) != 3:
            invalid.append(code)
            continue

        if code[0] not in {"1", "2", "3", "4", "5"}:
            invalid.append(code)
            continue

        upper_code = code.upper()

        if "X" in upper_code:
            if (
                upper_code[1:] == "XX"
                or (upper_code[1] == "X" and upper_code[2].isdigit())
                or (upper_code[1].isdigit() and upper_code[2] == "X")
            ):
                continue
            else:
                invalid.append(code)
                continue

        if not code.isnumeric():
            invalid.append(code)

    if invalid:
        raise ConfigError(
            f"Invalid status code(s): {', '.join(invalid)}. "
            "Use valid 3-digit codes between 100 and 599, "
            "or wildcards (e.g., 2XX, 2X0, 20X), where X is a wildcard digit."
        )
    return value


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
class MaxResponseTimeConfig(DiffBase):
    enabled: bool
    limit: float | None

    __slots__ = ("enabled", "limit")

    def __init__(self, *, limit: float | None = None) -> None:
        self.enabled = limit is not None
        self.limit = limit


@dataclass(repr=False)
class CheckConfig(DiffBase):
    enabled: bool
    expected_statuses: list[str]
    _DEFAULT_EXPECTED_STATUSES: ClassVar[list[str]]

    __slots__ = ("enabled", "expected_statuses")

    def __init__(self, *, enabled: bool = True, expected_statuses: Sequence[str | int] | None = None) -> None:
        self.enabled = enabled
        if expected_statuses is not None:
            statuses = [str(status) for status in expected_statuses]
            validate_status_codes(statuses)
            self.expected_statuses = statuses
        else:
            self.expected_statuses = self._DEFAULT_EXPECTED_STATUSES

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        enabled = data.get("enabled", True)
        return cls(
            enabled=enabled,
            expected_statuses=data.get("expected-statuses", cls._DEFAULT_EXPECTED_STATUSES),
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
    unsupported_method: SimpleCheckConfig
    max_response_time: MaxResponseTimeConfig
    _unknown: dict[str, SimpleCheckConfig]

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
        "unsupported_method",
        "max_response_time",
        "_unknown",
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
        unsupported_method: SimpleCheckConfig | None = None,
        max_response_time: MaxResponseTimeConfig | None = None,
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
        self.unsupported_method = unsupported_method or SimpleCheckConfig()
        self.max_response_time = max_response_time or MaxResponseTimeConfig()
        self._unknown = {}

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
            unsupported_method=SimpleCheckConfig.from_dict(merge(data.get("unsupported_method", {}))),
            max_response_time=MaxResponseTimeConfig(limit=data.get("max_response_time")),
        )

    def get_by_name(self, *, name: str) -> CheckConfig | SimpleCheckConfig | MaxResponseTimeConfig:
        try:
            return getattr(self, name)
        except AttributeError:
            return self._unknown.setdefault(name, SimpleCheckConfig())

    def update(
        self,
        *,
        included_check_names: list[str] | None = None,
        excluded_check_names: list[str] | None = None,
        max_response_time: float | None = None,
    ) -> None:
        known_names = {name for name in self.__slots__ if not name.startswith("_")}
        for name in known_names:
            # Check in explicitly excluded or not in explicitly included
            if name in (excluded_check_names or []) or (
                included_check_names is not None
                and "all" not in included_check_names
                and name not in included_check_names
            ):
                config = self.get_by_name(name=name)
                config.enabled = False
            elif included_check_names is not None and name in included_check_names:
                config = self.get_by_name(name=name)
                config.enabled = True

        if max_response_time is not None:
            self.max_response_time.enabled = True
            self.max_response_time.limit = max_response_time

        for name in included_check_names or []:
            if name not in known_names and name != "all":
                self._unknown[name] = SimpleCheckConfig(enabled=True)

        for name in excluded_check_names or []:
            if name not in known_names and name != "all":
                self._unknown[name] = SimpleCheckConfig(enabled=False)
