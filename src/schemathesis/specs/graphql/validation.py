from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, cast

import graphql
from graphql.execution.values import get_directive_values

from schemathesis.core.jsonschema.types import to_json_type_name
from schemathesis.core.output import pluralize
from schemathesis.core.transport import Response, load_json_lossy
from schemathesis.generation.case import Case
from schemathesis.graphql.checks import (
    GraphQLClientError,
    GraphQLDataMismatch,
    GraphQLSchemaViolation,
    GraphQLServerError,
    UnexpectedGraphQLResponse,
)
from schemathesis.specs.graphql._helpers import _root_type_for

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError

    from schemathesis.specs.graphql.schemas import GraphQLSchema


def parse_payload(response: Response) -> Any:
    """Parse a GraphQL response body, re-parsing from raw bytes if the response lies about its charset."""
    try:
        return response.json()
    except (LookupError, ValueError):
        return load_json_lossy(response.content, response.encoding)


# Error markers meaning the caller sent something the server refused, as emitted by Apollo Server (`code`)
# and graphql-java based servers (`classification`). Authentication and authorization codes are deliberately
# absent: a request that never reached the resolvers says nothing about the data it carried.
CLIENT_ERROR_MARKERS = frozenset(
    {
        "BAD_USER_INPUT",
        "GRAPHQL_VALIDATION_FAILED",
        "GRAPHQL_PARSE_FAILED",
        "BAD_REQUEST",
        "PERSISTED_QUERY_NOT_FOUND",
        "PERSISTED_QUERY_NOT_SUPPORTED",
        "OPERATION_RESOLUTION_FAILURE",
        "ValidationError",
        "InvalidSyntax",
        "OperationNotSupported",
    }
)
# Error markers meaning the server itself failed while serving the query
SERVER_ERROR_MARKERS = frozenset(
    {
        "INTERNAL_SERVER_ERROR",
        "DataFetchingException",
        "NullValueInNonNullableField",
    }
)


def _error_markers(errors: list[GraphQLFormattedError]) -> list[str]:
    """Every classification the server attached to its errors, in the order they appear."""
    return [
        marker
        for error in errors
        if isinstance(error, dict) and isinstance(error.get("extensions"), dict)
        for marker in (error["extensions"].get("code"), error["extensions"].get("classification"))
        if isinstance(marker, str)
    ]


def is_client_error(payload: dict) -> bool:
    """Check if the GraphQL response indicates a client error (query validation/syntax error).

    Client errors occur when the query itself is invalid (missing required args, wrong types, etc.)
    and the server refuses to serve it.

    Servers that label their errors are trusted, and a label reporting a server-side failure outweighs any
    rejection reported beside it. Otherwise, server errors have a `path` field pointing to the resolver that
    failed, or return partial `data`.
    """
    errors = payload.get("errors")
    if not errors or len(errors) == 0:
        return False
    markers = _error_markers(errors)
    if any(marker in SERVER_ERROR_MARKERS for marker in markers):
        return False
    if any(marker in CLIENT_ERROR_MARKERS for marker in markers):
        return True
    data = payload.get("data")
    # No `path` means the error occurred during query validation, not resolver execution
    return data is None and "path" not in errors[0]


# Depth, cost, alias, directive and token limits; a server that hides its limits sends a generic message instead
POLICY_REJECTION = re.compile(
    r"Syntax Error: (Query depth|Query Cost|Aliases|Directives|Token) limit of \d+ exceeded"
    r"|Syntax Error: Document contains more than \d+ tokens\. Parsing aborted\."
    r"|'[^']*' exceeds maximum operation depth of \d+"
    r"|\d+ aliases found\. Allowed: \d+"
)


def is_policy_rejection(errors: list[GraphQLFormattedError]) -> bool:
    """Check whether the server refused the query only because of its own limits, which says nothing about the data."""
    return all(isinstance(error, dict) and POLICY_REJECTION.match(error["message"]) for error in errors)


NON_NULLABLE_FIELD_MARKERS = (
    # graphql-core & graphql-js
    "Cannot return null for non-nullable field ",
    # graphql-java & Spring for GraphQL
    "was declared as a non null type, but the code involved in retrieving data has wrongly returned a null value",
)


# graphql-java labels this condition, so its wording does not have to be matched at all
NON_NULLABLE_FIELD_CLASSIFICATION = "NullValueInNonNullableField"


def _is_non_nullable_field_error(error: GraphQLFormattedError) -> bool:
    extensions = error.get("extensions")
    if isinstance(extensions, dict) and extensions.get("classification") == NON_NULLABLE_FIELD_CLASSIFICATION:
        return True
    return any(marker in error["message"] for marker in NON_NULLABLE_FIELD_MARKERS)


def is_schema_violation(errors: list[GraphQLFormattedError]) -> bool:
    """Check whether every error reports a field that resolved to null despite being declared non-nullable.

    Such a response contradicts the schema the server publishes. Responses that also carry unrelated errors stay
    plain server errors so that those are not hidden.
    """
    return bool(errors) and all(_is_non_nullable_field_error(error) for error in errors)


def validate_graphql_response(case: Case, payload: object) -> None:
    """Validate GraphQL response.

    Semantically valid GraphQL responses are JSON objects and may contain `data` or `errors` keys.
    """
    if not isinstance(payload, dict):
        raise UnexpectedGraphQLResponse(
            operation=case.operation.label,
            message="GraphQL response is not a JSON object",
            type_name=str(type(payload)),
        )

    errors = cast("list[GraphQLFormattedError]", payload.get("errors"))
    if errors is not None and len(errors) > 0:
        if is_policy_rejection(errors):
            return
        # Check if this is a client error (query validation failed)
        if is_client_error(payload):
            raise GraphQLClientError(operation=case.operation.label, message=errors[0]["message"], errors=errors)
        # Otherwise it's a server error (resolver execution failed)
        if len(errors) > 1:
            message = "\n\n".join([f"{idx}. {error['message']}" for idx, error in enumerate(errors, 1)])
        else:
            message = errors[0]["message"]
        # A server that breaks its own schema contract is an objectively diagnosable defect
        if is_schema_violation(errors):
            raise GraphQLSchemaViolation(operation=case.operation.label, message=message, errors=errors)
        raise GraphQLServerError(operation=case.operation.label, message=message, errors=errors)


Selections = Sequence[graphql.SelectionNode]
Path = tuple[str | int, ...]
# Identities of selection nodes, which the parsed query keeps alive.
NodeIds = tuple[int, ...]
# Response key -> field name and the selections merged from every node that requests it.
Fields = dict[str, tuple[str, Selections]]
JsonObject = dict[str, object]

# Execution resolves this field on every type without the schema declaring it.
TYPENAME_FIELD = "__typename"
# Directives that leave deferred fields and streamed items out of the initial payload.
INCREMENTAL_DELIVERY_DIRECTIVES = frozenset({"defer", "stream"})
# JSON types that serialize each built-in scalar; custom scalars can carry anything.
BUILTIN_SCALAR_TYPES: dict[graphql.GraphQLScalarType, tuple[type, ...]] = {
    graphql.GraphQLInt: (int, float),
    graphql.GraphQLFloat: (int, float),
    graphql.GraphQLString: (str,),
    graphql.GraphQLBoolean: (bool,),
    graphql.GraphQLID: (str, int),
}


@dataclass(frozen=True, slots=True)
class Mismatch:
    path: Path

    @property
    def message(self) -> str:
        raise NotImplementedError

    @property
    def line(self) -> str:
        return f"`{_format_path(self.path)}`: {self.message}"

    @property
    def subject(self) -> Path:
        return self.path

    @property
    def location(self) -> str:
        # List indices are dropped, so the same defect on another item is the same failure.
        return f"{type(self).__name__}:{'.'.join(segment for segment in self.subject if isinstance(segment, str))}"


@dataclass(frozen=True, slots=True)
class MissingData(Mismatch):
    @property
    def line(self) -> str:
        return "Response has neither `data` nor `errors`"


@dataclass(frozen=True, slots=True)
class FieldMismatch(Mismatch):
    field: str

    @property
    def subject(self) -> Path:
        return (*self.path, self.field)


@dataclass(frozen=True, slots=True)
class MissingField(FieldMismatch):
    @property
    def message(self) -> str:
        return f"missing field `{self.field}`"


@dataclass(frozen=True, slots=True)
class UnrequestedField(FieldMismatch):
    @property
    def message(self) -> str:
        return f"unrequested field `{self.field}`"


@dataclass(frozen=True, slots=True)
class WrongTypename(Mismatch):
    expected: str
    actual: object

    @property
    def message(self) -> str:
        return f"expected `{self.expected}`, got `{self.actual}`"


@dataclass(frozen=True, slots=True)
class ImpossibleTypename(Mismatch):
    typename: str
    abstract_type: str

    @property
    def message(self) -> str:
        return f"`{self.typename}` is not a possible type of `{self.abstract_type}`"


@dataclass(frozen=True, slots=True)
class NoPossibleType(Mismatch):
    abstract_type: str

    @property
    def message(self) -> str:
        return f"matches no possible type of `{self.abstract_type}`"


@dataclass(frozen=True, slots=True)
class NullForNonNull(Mismatch):
    type_name: str

    @property
    def message(self) -> str:
        return f"null for non-nullable `{self.type_name}`"


@dataclass(frozen=True, slots=True)
class NotAList(Mismatch):
    actual: str

    @property
    def message(self) -> str:
        return f"expected a list, got {self.actual}"


@dataclass(frozen=True, slots=True)
class NotAnObject(Mismatch):
    actual: str

    @property
    def message(self) -> str:
        return f"expected an object, got {self.actual}"


@dataclass(frozen=True, slots=True)
class WrongScalar(Mismatch):
    scalar: str
    actual: str

    @property
    def message(self) -> str:
        return f"expected `{self.scalar}`, got {self.actual}"


@dataclass(frozen=True, slots=True)
class IntOutOfRange(Mismatch):
    value: int

    @property
    def message(self) -> str:
        return f"`{self.value}` is outside the 32-bit range of `Int`"


@dataclass(frozen=True, slots=True)
class UnknownEnumValue(Mismatch):
    value: object
    enum: str

    @property
    def message(self) -> str:
        return f"`{self.value}` is not a value of enum `{self.enum}`"


def validate_data(case: Case, response: Response) -> None:
    """Check the `data` of an error-free response against the selection set of the query that produced it."""
    # Other statuses may come from a proxy or a framework that sends no GraphQL response at all.
    if not 200 <= response.status_code < 300:
        return
    payload = parse_payload(response)
    if not isinstance(payload, dict) or payload.get("errors"):
        return
    if "data" not in payload:
        _raise(case, [MissingData(())])
    data = payload["data"]
    # `null` means nothing was returned, which the missing test data warning reports.
    if data is None:
        return
    if not isinstance(data, dict):
        _raise(case, [NotAnObject((), to_json_type_name(data))])
    mismatches = _check_against_query(case, data)
    if mismatches:
        _raise(case, mismatches)


def _check_against_query(case: Case, data: JsonObject) -> list[Mismatch]:
    if not isinstance(case.body, str):
        return []
    try:
        document = graphql.parse(case.body)
    except graphql.GraphQLSyntaxError:
        return []
    operations = [node for node in document.definitions if isinstance(node, graphql.OperationDefinitionNode)]
    # Queries with variables are skipped: `@skip` and `@include` would need their values to pick the fields.
    if len(operations) != 1 or operations[0].variable_definitions:
        return []
    if _uses_incremental_delivery(document):
        return []
    schema = cast("GraphQLSchema", case.operation.schema).client_schema
    root = _root_type_for(schema, operations[0].operation)
    if root is None:
        return []
    fragments = {
        node.name.value: node for node in document.definitions if isinstance(node, graphql.FragmentDefinitionNode)
    }
    return _DataWalker(schema, fragments).check_object(operations[0].selection_set.selections, root, data, ())


def _raise(case: Case, mismatches: list[Mismatch]) -> NoReturn:
    # The same defect on many list items is one line, as it is one failure.
    groups: dict[str, list[Mismatch]] = {}
    for mismatch in mismatches:
        groups.setdefault(mismatch.location, []).append(mismatch)
    lines = []
    for group in groups.values():
        line = group[0].line
        if len(group) > 1:
            line += f" (and {pluralize(len(group) - 1, 'more item')})"
        lines.append(line)
    raise GraphQLDataMismatch(operation=case.operation.label, message="\n".join(lines), locations=list(groups))


class _IncrementalDelivery(graphql.Visitor):
    def __init__(self) -> None:
        super().__init__()
        self.found = False

    def enter_directive(self, node: graphql.DirectiveNode, *args: object) -> None:
        if node.name.value in INCREMENTAL_DELIVERY_DIRECTIVES:
            self.found = True


def _uses_incremental_delivery(document: graphql.DocumentNode) -> bool:
    visitor = _IncrementalDelivery()
    graphql.visit(document, visitor)
    return visitor.found


def _format_path(path: Path) -> str:
    if not path:
        return "data"
    rendered = ""
    for segment in path:
        if isinstance(segment, int):
            rendered += f"[{segment}]"
        else:
            rendered += f".{segment}" if rendered else segment
    return rendered


def _check_scalar(scalar: graphql.GraphQLScalarType, value: object, path: Path) -> list[Mismatch]:
    expected = BUILTIN_SCALAR_TYPES.get(scalar)
    if expected is None:
        return []
    # `true` is an `int` in Python, but not a number in JSON.
    if not isinstance(value, expected) or isinstance(value, bool) != (scalar is graphql.GraphQLBoolean):
        return [WrongScalar(path, scalar.name, to_json_type_name(value))]
    if scalar is graphql.GraphQLInt:
        # JSON has one number type, so `1.0` is as much an integer as `1`.
        if isinstance(value, float):
            if not value.is_integer():
                return [WrongScalar(path, scalar.name, to_json_type_name(value))]
            value = int(value)
        if isinstance(value, int) and not graphql.GRAPHQL_MIN_INT <= value <= graphql.GRAPHQL_MAX_INT:
            return [IntOutOfRange(path, value)]
    return []


class _DataWalker:
    __slots__ = ("schema", "fragments", "_possible_types", "_fields", "_checked")

    def __init__(self, schema: graphql.GraphQLSchema, fragments: dict[str, graphql.FragmentDefinitionNode]) -> None:
        self.schema = schema
        self.fragments = fragments
        self._possible_types: dict[str, list[graphql.GraphQLObjectType]] = {}
        self._fields: dict[tuple[NodeIds, str], Fields] = {}
        self._checked: dict[tuple[Path, str, NodeIds], list[Mismatch]] = {}

    def check_object(
        self, selections: Selections, object_type: graphql.GraphQLObjectType, value: JsonObject, path: Path
    ) -> list[Mismatch]:
        # A value without `__typename` is checked against every possible type, so nested checks are memoized.
        key = (path, object_type.name, tuple(map(id, selections)))
        checked = self._checked.get(key)
        if checked is not None:
            return checked
        fields = self._collect_fields(selections, object_type)
        mismatches: list[Mismatch] = [UnrequestedField(path, name) for name in value if name not in fields]
        for response_key, (name, sub_selections) in fields.items():
            if response_key not in value:
                mismatches.append(MissingField(path, response_key))
                continue
            item = value[response_key]
            if name == TYPENAME_FIELD:
                if item != object_type.name:
                    mismatches.append(WrongTypename((*path, response_key), object_type.name, item))
                continue
            field_definition = object_type.fields.get(name)
            if field_definition is None:
                continue
            mismatches.extend(self.check_value(field_definition.type, sub_selections, item, (*path, response_key)))
        self._checked[key] = mismatches
        return mismatches

    def check_value(
        self, type_: graphql.GraphQLOutputType, selections: Selections, value: object, path: Path
    ) -> list[Mismatch]:
        if isinstance(type_, graphql.GraphQLNonNull):
            if value is None:
                return [NullForNonNull(path, str(type_))]
            return self.check_value(type_.of_type, selections, value, path)
        if value is None:
            return []
        if isinstance(type_, graphql.GraphQLList):
            if not isinstance(value, list):
                return [NotAList(path, to_json_type_name(value))]
            return [
                mismatch
                for index, item in enumerate(value)
                for mismatch in self.check_value(type_.of_type, selections, item, (*path, index))
            ]
        if isinstance(type_, graphql.GraphQLScalarType):
            return _check_scalar(type_, value, path)
        if isinstance(type_, graphql.GraphQLEnumType):
            if not isinstance(value, str) or value not in type_.values:
                return [UnknownEnumValue(path, value, type_.name)]
            return []
        if not isinstance(value, dict):
            return [NotAnObject(path, to_json_type_name(value))]
        if isinstance(type_, graphql.GraphQLObjectType):
            return self.check_object(selections, type_, value, path)
        return self._check_abstract(type_, selections, value, path)

    def _check_abstract(
        self,
        type_: graphql.GraphQLInterfaceType | graphql.GraphQLUnionType,
        selections: Selections,
        value: JsonObject,
        path: Path,
    ) -> list[Mismatch]:
        possible = self._possible_types.get(type_.name)
        if possible is None:
            possible = sorted(self.schema.get_possible_types(type_), key=lambda candidate: candidate.name)
            self._possible_types[type_.name] = possible
        typename = value.get(TYPENAME_FIELD)
        if isinstance(typename, str):
            for candidate in possible:
                if candidate.name == typename:
                    return self.check_object(selections, candidate, value, path)
            return [ImpossibleTypename((*path, TYPENAME_FIELD), typename, type_.name)]
        # Without `__typename` the runtime type is unknown, so any possible type the data fits will do.
        failures = []
        for candidate in possible:
            mismatches = self.check_object(selections, candidate, value, path)
            if not mismatches:
                return []
            failures.append(mismatches)
        # Types whose fields match the value's keys tell what is wrong inside it, if they all agree.
        fitting = [
            mismatches
            for mismatches in failures
            if not any(isinstance(mismatch, FieldMismatch) and mismatch.path == path for mismatch in mismatches)
        ] or failures
        if fitting and all(mismatches == fitting[0] for mismatches in fitting):
            return fitting[0]
        return [NoPossibleType(path, type_.name)]

    def _collect_fields(self, selections: Selections, runtime_type: graphql.GraphQLObjectType) -> Fields:
        """The fields a selection set requests from `runtime_type`, grouped as execution groups them."""
        key = (tuple(map(id, selections)), runtime_type.name)
        fields = self._fields.get(key)
        if fields is None:
            nodes: dict[str, list[graphql.FieldNode]] = {}
            self._collect(selections, runtime_type, nodes, set())
            fields = {
                response_key: (
                    group[0].name.value,
                    tuple(
                        selection for node in group if node.selection_set for selection in node.selection_set.selections
                    ),
                )
                for response_key, group in nodes.items()
            }
            self._fields[key] = fields
        return fields

    def _collect(
        self,
        selections: Selections,
        runtime_type: graphql.GraphQLObjectType,
        fields: dict[str, list[graphql.FieldNode]],
        visited_fragments: set[str],
    ) -> None:
        for selection in selections:
            if not _is_included(selection):
                continue
            if isinstance(selection, graphql.FieldNode):
                response_key = selection.alias.value if selection.alias else selection.name.value
                fields.setdefault(response_key, []).append(selection)
            elif isinstance(selection, graphql.InlineFragmentNode):
                if selection.type_condition is None or self._applies(selection.type_condition, runtime_type):
                    self._collect(selection.selection_set.selections, runtime_type, fields, visited_fragments)
            else:
                name = cast(graphql.FragmentSpreadNode, selection).name.value
                # Each fragment is collected once, which also ends cycles that the server's validation would reject.
                if name in visited_fragments:
                    continue
                visited_fragments.add(name)
                fragment = self.fragments.get(name)
                if fragment is not None and self._applies(fragment.type_condition, runtime_type):
                    self._collect(fragment.selection_set.selections, runtime_type, fields, visited_fragments)

    def _applies(self, condition: graphql.NamedTypeNode, runtime_type: graphql.GraphQLObjectType) -> bool:
        condition_type = self.schema.get_type(condition.name.value)
        if condition_type is runtime_type:
            return True
        return isinstance(condition_type, (graphql.GraphQLInterfaceType, graphql.GraphQLUnionType)) and (
            self.schema.is_sub_type(condition_type, runtime_type)
        )


def _is_included(selection: graphql.SelectionNode) -> bool:
    try:
        skip = get_directive_values(graphql.GraphQLSkipDirective, selection)
        include = get_directive_values(graphql.GraphQLIncludeDirective, selection)
    except graphql.GraphQLError:
        # A server rejects a malformed condition, so the field stays as if the directive were absent.
        return True
    return not (skip is not None and skip["if"]) and not (include is not None and not include["if"])
