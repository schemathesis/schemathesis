import enum


class NotSet:
    pass


NOT_SET = NotSet()


class Specification(str, enum.Enum):
    """Specification of the given schema."""

    OPENAPI = "openapi"
    GRAPHQL = "graphql"
