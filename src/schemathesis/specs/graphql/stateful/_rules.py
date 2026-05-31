"""Generate Hypothesis stateful rules for GraphQL operations.

For each id-typed object type, the state machine has a `<TypeName>_ids` bundle
plus a parallel `deleted_<TypeName>_ids` bundle. Producer rules call mutations
whose return type unwraps to a bundle type, extract captured `id` values from
the response, and feed them back into that bundle. Consumer rules accept ids
from a bundle as input and call the underlying operation, recording the parent
case so failure traces can be reconstructed. Cleanup rules consume from the
alive bundle and target the deleted bundle. Probe rules re-fire cleanup or
read-only operations against deleted ids to surface mishandling of stale
identifiers (use-after-delete reads, double-delete errors).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import graphql
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, consumes, multiple, precondition, rule

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.generation.meta import TestPhase
from schemathesis.generation.stateful.state_machine import BASE_EXPLORATION_RATE
from schemathesis.specs.graphql._helpers import _unwrap, relay_node_type
from schemathesis.specs.graphql.handles import Handle, SchemaIndex, bundle_name, deleted_bundle_name
from schemathesis.specs.graphql.inference import (
    CLEANUP_PREFIXES,
    OperationRole,
    RootType,
    classify_operation,
    extract_entity,
)
from schemathesis.specs.graphql.stateful._extract import iter_handle_values
from schemathesis.specs.graphql.substitution import candidate_handle, substitute_bundle_values

if TYPE_CHECKING:
    from random import Random

    from schemathesis.specs.graphql.schemas import GraphQLOperationDefinition, GraphQLSchema


@dataclass(slots=True)
class _OperationSummary:
    label: str
    role: OperationRole
    return_type_name: str | None
    consumed: dict[str, Handle]
    field_name: str
    root_type: RootType
    # Object return type name (the Relay node type for connections); drives non-id producers.
    unwrapped_return_name: str | None
    # Whether the return is a Relay connection, so capture/injection go through `edges { node }`.
    returns_connection: bool


@dataclass(slots=True)
class _RuleSet:
    """Decorated rule callables and the names of producer rules among them."""

    rules: list[tuple[str, Callable]]
    producer_names: frozenset[str]


def iter_operation_summaries(
    schema: GraphQLSchema,
    handles: set[Handle],
    index: SchemaIndex,
) -> Iterator[_OperationSummary]:
    """Yield a summary per non-skipped Query/Mutation operation."""
    id_types = {handle.type_name for handle in handles if handle.field_name == "id"}
    for result in schema.get_all_operations():
        if not isinstance(result, Ok):
            # GraphQL builds the whole client schema upfront, so per-operation errors never occur here.
            continue  # pragma: no cover
        operation = result.ok()
        definition = cast("GraphQLOperationDefinition", operation.definition)
        field_def = cast(graphql.GraphQLField, definition.raw)
        field_name = definition.field_name
        root_type = definition.root_type
        role = classify_operation(field_name=field_name, root_type=root_type)
        unwrapped_return = _unwrap(field_def.type)
        node = relay_node_type(unwrapped_return)
        return_object = node if node is not None else unwrapped_return
        unwrapped_return_name = return_object.name if isinstance(return_object, graphql.GraphQLObjectType) else None
        return_type_name: str | None = unwrapped_return.name if unwrapped_return.name in id_types else None

        cleanup_entity: str | None = None
        if role == OperationRole.CLEANUP:
            cleanup_entity = extract_entity(field_name, prefixes=CLEANUP_PREFIXES)

        consumed: dict[str, Handle] = {}
        for argument_name, argument in field_def.args.items():
            scalar_name = _unwrap(argument.type).name
            handle = candidate_handle(
                scalar_name=scalar_name,
                argument_name=argument_name,
                enclosing_field_type=unwrapped_return_name,
                index=index,
            )
            if handle is None or handle not in handles:
                # Fallback: cleanup ops typically return Boolean, so the entity
                # name has to come from the field name itself (`deleteBook` -> `Book`).
                if cleanup_entity is not None and scalar_name == "ID" and argument_name in ("id", "ids"):
                    if Handle(cleanup_entity, "id") in handles:
                        consumed[argument_name] = Handle(cleanup_entity, "id")
                continue
            consumed[argument_name] = handle

        yield _OperationSummary(
            label=operation.label,
            role=role,
            return_type_name=return_type_name,
            consumed=consumed,
            field_name=field_name,
            root_type=root_type,
            unwrapped_return_name=unwrapped_return_name,
            returns_connection=node is not None,
        )


def producers_by_handle(
    summaries: list[_OperationSummary],
    handles: set[Handle],
    index: SchemaIndex,
) -> dict[Handle, list[str]]:
    """Map each handle to the labels of operations that can produce it.

    Create-verb mutations produce their id handle; any operation returning a type
    produces that type's wanted non-id handles (the field is injected on capture).
    """
    non_id_handles = {handle for handle in handles if handle.field_name != "id"}
    result: dict[Handle, list[str]] = {}
    for summary in summaries:
        if summary.role == OperationRole.PRODUCER and summary.return_type_name is not None:
            result.setdefault(Handle(summary.return_type_name, "id"), []).append(summary.label)
        if summary.unwrapped_return_name is not None:
            for handle in non_id_handles:
                if (
                    handle.type_name == summary.unwrapped_return_name
                    and handle.field_name in index.leaf_string_id_fields(handle.type_name)
                ):
                    result.setdefault(handle, []).append(summary.label)
    return result


def generate_rules_for(
    summaries: list[_OperationSummary],
    attrs: dict[str, Any],
    handles: set[Handle],
    index: SchemaIndex,
) -> _RuleSet:
    """Build all stateful rules and report which of them are producers."""
    producers = producers_by_handle(summaries, handles, index)
    summary_by_label = {summary.label: summary for summary in summaries}

    # Lifecycle (cleanup/use-after-delete/double-delete) stays id-only; non-id handles are read-only.
    cleanup_types: set[Handle] = set()
    for summary in summaries:
        if summary.role == OperationRole.CLEANUP and summary.consumed:
            primary_handle = next(iter(summary.consumed.values()))
            if primary_handle.field_name == "id":
                cleanup_types.add(primary_handle)

    generated: list[tuple[str, Callable]] = []
    producer_names: list[str] = []
    for summary in summaries:
        root_label = summary.label.split(".", 1)[0]
        if summary.role == OperationRole.PRODUCER and summary.return_type_name is not None:
            entry = _make_producer_rule(
                field_name=summary.field_name,
                return_type_name=summary.return_type_name,
                root_label=root_label,
                attrs=attrs,
            )
            generated.append(entry)
            producer_names.append(entry[0])
        elif summary.consumed:
            primary_handle = next(iter(summary.consumed.values()))
            # A cleanup op keyed on a non-id handle has no deleted bundle, so it acts as a plain consumer.
            if summary.role == OperationRole.CLEANUP and primary_handle.field_name == "id":
                generated.append(
                    _make_cleanup_rule(
                        field_name=summary.field_name,
                        consumed=summary.consumed,
                        root_label=root_label,
                        attrs=attrs,
                    )
                )
                generated.append(
                    _make_double_cleanup_rule(
                        field_name=summary.field_name,
                        consumed=summary.consumed,
                        root_label=root_label,
                        attrs=attrs,
                    )
                )
            else:
                generated.append(
                    _make_consumer_rule(
                        field_name=summary.field_name,
                        consumed=summary.consumed,
                        root_label=root_label,
                        attrs=attrs,
                        producers_by_handle=producers,
                    )
                )
                if primary_handle in cleanup_types:
                    generated.append(
                        _make_use_after_delete_rule(
                            field_name=summary.field_name,
                            consumed=summary.consumed,
                            root_label=root_label,
                            attrs=attrs,
                        )
                    )

    for handle, labels in producers.items():
        if handle.field_name == "id":
            continue
        for label in labels:
            summary = summary_by_label[label]
            entry = _make_handle_producer_rule(
                field_name=summary.field_name,
                handle=handle,
                root_label=label.split(".", 1)[0],
                attrs=attrs,
                via_edges=summary.returns_connection,
            )
            generated.append(entry)
            producer_names.append(entry[0])

    return _RuleSet(rules=generated, producer_names=frozenset(producer_names))


def _bundle_substituter(
    schema: GraphQLSchema,
    consumed: dict[str, Handle],
    bundle_args: dict[str, Any],
    *,
    exploration_rate: float,
) -> Callable[[graphql.OperationDefinitionNode, Random], None]:
    """Build the `mutate_ast` callback that swaps strategy-generated id literals with bundle values."""
    bundle_values = {consumed[argument]: bundle_args[argument] for argument in bundle_args}

    def mutate(operation_node: graphql.OperationDefinitionNode, random: Random) -> None:
        substitute_bundle_values(
            operation_node=operation_node,
            client_schema=schema.client_schema,
            bundle_values=bundle_values,
            random=random,
            exploration_rate=exploration_rate,
            schema_index=schema.analysis.schema_index,
        )

    return mutate


def _make_producer_rule(
    *,
    field_name: str,
    return_type_name: str,
    root_label: str,
    attrs: dict[str, Any],
) -> tuple[str, Callable]:
    rule_name = f"{root_label}_{field_name}"
    producer_label = f"{root_label}.{field_name}"
    target_bundle = attrs[bundle_name(Handle(return_type_name, "id"))]

    def body(self: Any, data: st.DataObject) -> Any:
        operation = self.schema[root_label][field_name]
        from schemathesis.specs.graphql.schemas import graphql_cases

        case = data.draw(graphql_cases(operation=operation, hooks=self.schema.hooks, phase=TestPhase.STATEFUL))
        output = self._run_case(case, rule_name=rule_name, parent_id=None, applied_parameters=None)
        values = [
            value
            for _field, value in iter_handle_values(
                output.response.content, field_name=field_name, handle_fields=frozenset({"id"})
            )
        ]
        if not values:
            return multiple()
        for value in values:
            self._id_origins[(Handle(return_type_name, "id"), value)] = case.id
        return multiple(*values)

    def _root_precondition(self: Any, _label: str = producer_label) -> bool:
        return self.control.allow_root_transition(_label, self.bundles)

    body.__name__ = rule_name
    decorated = rule(target=target_bundle, data=st.data())(body)
    return rule_name, precondition(_root_precondition)(decorated)


def _inject_field(
    field_name: str, handle_field: str, *, via_edges: bool
) -> Callable[[graphql.OperationDefinitionNode, Random], None]:
    """Ensure `handle_field` is selected inside the producing field, so its value can be captured.

    For Relay connections the field is nested under `edges { node { ... } }`.
    """
    path = ["edges", "node", handle_field] if via_edges else [handle_field]

    def mutate(operation_node: graphql.OperationDefinitionNode, _random: Random) -> None:
        for selection in operation_node.selection_set.selections:
            if isinstance(selection, graphql.FieldNode) and selection.name.value == field_name:
                if selection.selection_set is not None:
                    _ensure_selection_path(selection.selection_set, path)
                return

    return mutate


def _ensure_selection_path(selection_set: graphql.SelectionSetNode, path: list[str]) -> None:
    name, rest = path[0], path[1:]
    field = next(
        (node for node in selection_set.selections if isinstance(node, graphql.FieldNode) and node.name.value == name),
        None,
    )
    if field is None:
        nested = graphql.SelectionSetNode(selections=()) if rest else None
        field = graphql.FieldNode(name=graphql.NameNode(value=name), selection_set=nested)
        selection_set.selections = (*selection_set.selections, field)
    if rest:
        if field.selection_set is None:
            field.selection_set = graphql.SelectionSetNode(selections=())
        _ensure_selection_path(field.selection_set, rest)


def _make_handle_producer_rule(
    *,
    field_name: str,
    handle: Handle,
    root_label: str,
    attrs: dict[str, Any],
    via_edges: bool,
) -> tuple[str, Callable]:
    rule_name = f"{root_label}_{field_name}__{handle.field_name}"
    target_bundle = attrs[bundle_name(handle)]

    def body(self: Any, data: st.DataObject) -> Any:
        operation = self.schema[root_label][field_name]
        from schemathesis.specs.graphql.schemas import graphql_cases

        case = data.draw(
            graphql_cases(
                operation=operation,
                hooks=self.schema.hooks,
                phase=TestPhase.STATEFUL,
                mutate_ast=_inject_field(field_name, handle.field_name, via_edges=via_edges),
            )
        )
        output = self._run_case(case, rule_name=rule_name, parent_id=None, applied_parameters=None)
        values = [
            value
            for _field, value in iter_handle_values(
                output.response.content, field_name=field_name, handle_fields=frozenset({handle.field_name})
            )
        ]
        if not values:
            return multiple()
        for value in values:
            self._id_origins[(handle, value)] = case.id
        return multiple(*values)

    body.__name__ = rule_name
    decorated = rule(target=target_bundle, data=st.data())(body)
    return rule_name, decorated


def _make_consumer_rule(
    *,
    field_name: str,
    consumed: dict[str, Handle],
    root_label: str,
    attrs: dict[str, Any],
    producers_by_handle: dict[Handle, list[str]],
) -> tuple[str, Callable]:
    rule_name = f"{root_label}_{field_name}"
    consumer_label = f"{root_label}.{field_name}"
    bundle_kwargs: dict[str, Bundle] = {
        argument_name: attrs[bundle_name(handle)] for argument_name, handle in consumed.items()
    }
    primary_argument = next(iter(consumed))
    parent_handle = consumed[primary_argument]
    feeding_producers: list[str] = []
    for handle in consumed.values():
        feeding_producers.extend(producers_by_handle.get(handle, []))

    def body(self: Any, data: st.DataObject, **bundle_args: Any) -> None:
        from schemathesis.specs.graphql.schemas import graphql_cases

        operation = self.schema[root_label][field_name]
        case = data.draw(
            graphql_cases(
                operation=operation,
                hooks=self.schema.hooks,
                phase=TestPhase.STATEFUL,
                mutate_ast=_bundle_substituter(
                    self.schema, consumed, bundle_args, exploration_rate=BASE_EXPLORATION_RATE
                ),
            )
        )
        parent_id = self._id_origins.get((parent_handle, bundle_args[primary_argument]))
        self._run_case(
            case,
            rule_name=rule_name,
            parent_id=parent_id,
            applied_parameters=[(ParameterLocation.BODY, name) for name in bundle_args],
        )

    def _consumer_precondition(
        self: Any,
        _producers: tuple[str, ...] = tuple(feeding_producers),
        _consumer: str = consumer_label,
    ) -> bool:
        if not _producers:
            return True
        return any(self.control.allow_transition(producer, _consumer) for producer in _producers)

    body.__name__ = rule_name
    decorated = rule(data=st.data(), **bundle_kwargs)(body)
    return rule_name, precondition(_consumer_precondition)(decorated)


def _alive_origin(machine: Any) -> dict[tuple[Handle, str], str]:
    return machine._id_origins


def _deleted_origin(machine: Any) -> dict[tuple[Handle, str], str]:
    return machine._deleted_id_origins


def _make_lifecycle_rule(
    *,
    field_name: str,
    root_label: str,
    consumed: dict[str, Handle],
    attrs: dict[str, Any],
    rule_name_suffix: str,
    primary_source: Any,
    target_bundle: Any | None,
    origin: Callable[[Any], dict[tuple[Handle, str], str]],
    track_deletion: bool,
) -> tuple[str, Callable]:
    """Build a rule that operates against a specific bundle for the primary id-typed argument.

    Used by cleanup (consumes alive, emits to deleted), double-cleanup probe
    (consumes deleted), and use-after-delete probe (reads deleted without consuming).
    Non-primary id arguments are sourced from their alive bundles.
    """
    rule_name = f"{root_label}_{field_name}{rule_name_suffix}"
    primary_argument = next(iter(consumed))
    primary_handle = consumed[primary_argument]

    bundle_kwargs: dict[str, Any] = {primary_argument: primary_source}
    for argument_name, handle in consumed.items():
        if argument_name == primary_argument:
            continue
        bundle_kwargs[argument_name] = attrs[bundle_name(handle)]

    def body(self: Any, data: st.DataObject, **bundle_args: Any) -> Any:
        from schemathesis.specs.graphql.schemas import graphql_cases

        operation = self.schema[root_label][field_name]
        case = data.draw(
            graphql_cases(
                operation=operation,
                hooks=self.schema.hooks,
                phase=TestPhase.STATEFUL,
                mutate_ast=_bundle_substituter(self.schema, consumed, bundle_args, exploration_rate=0.0),
            )
        )
        primary_id = bundle_args[primary_argument]
        parent_id = origin(self).get((primary_handle, primary_id))
        self._run_case(
            case,
            rule_name=rule_name,
            parent_id=parent_id,
            applied_parameters=[(ParameterLocation.BODY, name) for name in bundle_args],
        )
        if track_deletion:
            # Track the deleted id's origin so use-after-delete probes can stitch parentage.
            self._deleted_id_origins[(primary_handle, primary_id)] = case.id
            return primary_id
        return None

    body.__name__ = rule_name
    if target_bundle is not None:
        return rule_name, rule(target=target_bundle, data=st.data(), **bundle_kwargs)(body)
    return rule_name, rule(data=st.data(), **bundle_kwargs)(body)


def _make_cleanup_rule(
    *, field_name: str, consumed: dict[str, Handle], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Cleanup mutation: consumes from the alive bundle, emits into the deleted bundle."""
    primary_handle = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="",
        primary_source=consumes(attrs[bundle_name(primary_handle)]),
        target_bundle=attrs[deleted_bundle_name(primary_handle)],
        origin=_alive_origin,
        track_deletion=True,
    )


def _make_double_cleanup_rule(
    *, field_name: str, consumed: dict[str, Handle], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Probe: re-fires the cleanup against an already-deleted id (consumed once per id)."""
    primary_handle = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="_double",
        primary_source=consumes(attrs[deleted_bundle_name(primary_handle)]),
        target_bundle=None,
        origin=_deleted_origin,
        track_deletion=False,
    )


def _make_use_after_delete_rule(
    *, field_name: str, consumed: dict[str, Handle], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Probe: re-targets a non-cleanup operation at a known-deleted id (no consume)."""
    primary_handle = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="_on_deleted",
        primary_source=attrs[deleted_bundle_name(primary_handle)],
        target_bundle=None,
        origin=_deleted_origin,
        track_deletion=False,
    )
