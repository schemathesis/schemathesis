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

from schemathesis.core.result import Ok
from schemathesis.generation.meta import TestPhase
from schemathesis.generation.stateful.state_machine import BASE_EXPLORATION_RATE
from schemathesis.specs.graphql._helpers import _unwrap
from schemathesis.specs.graphql.inference import (
    CLEANUP_PREFIXES,
    OperationRole,
    RootType,
    classify_operation,
    extract_entity,
)
from schemathesis.specs.graphql.stateful._extract import iter_ids_from_response
from schemathesis.specs.graphql.substitution import _candidate_parent_type, substitute_bundle_values

if TYPE_CHECKING:
    from random import Random

    from schemathesis.specs.graphql.schemas import GraphQLOperationDefinition, GraphQLSchema


@dataclass(slots=True)
class _OperationSummary:
    label: str
    role: OperationRole
    return_type_name: str | None
    consumed: dict[str, str]
    field_name: str
    root_type: RootType


@dataclass(slots=True)
class _RuleSet:
    """Decorated rule callables and the names of producer rules among them."""

    rules: list[tuple[str, Callable]]
    producer_names: frozenset[str]


def iter_operation_summaries(
    schema: GraphQLSchema,
    bundle_types: set[str],
) -> Iterator[_OperationSummary]:
    """Yield a summary per non-skipped Query/Mutation operation."""
    for result in schema.get_all_operations():
        if not isinstance(result, Ok):
            continue
        operation = result.ok()
        definition = cast("GraphQLOperationDefinition", operation.definition)
        field_def = cast(graphql.GraphQLField, definition.raw)
        field_name = definition.field_name
        root_type = definition.root_type
        role = classify_operation(field_name=field_name, root_type=root_type)
        unwrapped_return = _unwrap(field_def.type)
        return_type_name: str | None = unwrapped_return.name if unwrapped_return.name in bundle_types else None

        cleanup_entity: str | None = None
        if role == OperationRole.CLEANUP:
            cleanup_entity = extract_entity(field_name, prefixes=CLEANUP_PREFIXES)

        consumed: dict[str, str] = {}
        for argument_name, argument in field_def.args.items():
            scalar_name = _unwrap(argument.type).name
            parent_type = _candidate_parent_type(
                scalar_name=scalar_name,
                argument_name=argument_name,
                enclosing_field_type=return_type_name,
            )
            if parent_type is None or parent_type not in bundle_types:
                # Fallback: cleanup ops typically return Boolean, so the entity
                # name has to come from the field name itself (`deleteBook` -> `Book`).
                if cleanup_entity is not None and scalar_name == "ID" and argument_name in ("id", "ids"):
                    if cleanup_entity in bundle_types:
                        consumed[argument_name] = cleanup_entity
                continue
            consumed[argument_name] = parent_type

        yield _OperationSummary(
            label=operation.label,
            role=role,
            return_type_name=return_type_name,
            consumed=consumed,
            field_name=field_name,
            root_type=root_type,
        )


def generate_rules_for(
    summaries: list[_OperationSummary],
    attrs: dict[str, Any],
) -> _RuleSet:
    """Build all stateful rules and report which of them are producers."""
    producers_by_type: dict[str, list[str]] = {}
    for summary in summaries:
        if summary.role == OperationRole.PRODUCER and summary.return_type_name is not None:
            producers_by_type.setdefault(summary.return_type_name, []).append(summary.label)

    cleanup_types: set[str] = set()
    for summary in summaries:
        if summary.role == OperationRole.CLEANUP and summary.consumed:
            primary_type = next(iter(summary.consumed.values()))
            cleanup_types.add(primary_type)

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
        elif summary.role in (OperationRole.READER, OperationRole.MUTATOR) and summary.consumed:
            generated.append(
                _make_consumer_rule(
                    field_name=summary.field_name,
                    consumed=summary.consumed,
                    root_label=root_label,
                    attrs=attrs,
                    producers_by_type=producers_by_type,
                )
            )
            primary_type = next(iter(summary.consumed.values()))
            if primary_type in cleanup_types:
                generated.append(
                    _make_use_after_delete_rule(
                        field_name=summary.field_name,
                        consumed=summary.consumed,
                        root_label=root_label,
                        attrs=attrs,
                    )
                )
        elif summary.role == OperationRole.CLEANUP and summary.consumed:
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
    return _RuleSet(rules=generated, producer_names=frozenset(producer_names))


def _bundle_substituter(
    schema: GraphQLSchema,
    consumed: dict[str, str],
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
    target_bundle = attrs[f"{return_type_name}_ids"]

    def body(self: Any, data: st.DataObject) -> Any:
        operation = self.schema[root_label][field_name]
        from schemathesis.specs.graphql.schemas import graphql_cases

        case = data.draw(graphql_cases(operation=operation, hooks=self.schema.hooks, phase=TestPhase.STATEFUL))
        output = self._run_case(case, rule_name=rule_name, parent_id=None, applied_parameters=None)
        ids = list(iter_ids_from_response(output.response.content, field_name=field_name))
        if not ids:
            return multiple()
        for value in ids:
            self._id_origins[(return_type_name, value)] = case.id
        return multiple(*ids)

    def _root_precondition(self: Any, _label: str = producer_label) -> bool:
        return self.control.allow_root_transition(_label, self.bundles)

    body.__name__ = rule_name
    decorated = rule(target=target_bundle, data=st.data())(body)
    return rule_name, precondition(_root_precondition)(decorated)


def _make_consumer_rule(
    *,
    field_name: str,
    consumed: dict[str, str],
    root_label: str,
    attrs: dict[str, Any],
    producers_by_type: dict[str, list[str]],
) -> tuple[str, Callable]:
    rule_name = f"{root_label}_{field_name}"
    consumer_label = f"{root_label}.{field_name}"
    bundle_kwargs: dict[str, Bundle] = {
        argument_name: attrs[f"{type_name}_ids"] for argument_name, type_name in consumed.items()
    }
    primary_argument = next(iter(consumed))
    parent_type_name = consumed[primary_argument]
    feeding_producers: list[str] = []
    for parent_type in consumed.values():
        feeding_producers.extend(producers_by_type.get(parent_type, []))

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
        parent_id = self._id_origins.get((parent_type_name, bundle_args[primary_argument]))
        self._run_case(
            case,
            rule_name=rule_name,
            parent_id=parent_id,
            applied_parameters=list(bundle_args.keys()),
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


def _alive_origin(machine: Any) -> dict[tuple[str, str], str]:
    return machine._id_origins


def _deleted_origin(machine: Any) -> dict[tuple[str, str], str]:
    return machine._deleted_id_origins


def _make_lifecycle_rule(
    *,
    field_name: str,
    root_label: str,
    consumed: dict[str, str],
    attrs: dict[str, Any],
    rule_name_suffix: str,
    primary_source: Any,
    target_bundle: Any | None,
    origin: Callable[[Any], dict[tuple[str, str], str]],
    track_deletion: bool,
) -> tuple[str, Callable]:
    """Build a rule that operates against a specific bundle for the primary id-typed argument.

    Used by cleanup (consumes alive, emits to deleted), double-cleanup probe
    (consumes deleted), and use-after-delete probe (reads deleted without consuming).
    Non-primary id arguments are sourced from their alive bundles.
    """
    rule_name = f"{root_label}_{field_name}{rule_name_suffix}"
    primary_argument = next(iter(consumed))
    primary_type = consumed[primary_argument]

    bundle_kwargs: dict[str, Any] = {primary_argument: primary_source}
    for argument_name, type_name in consumed.items():
        if argument_name == primary_argument:
            continue
        bundle_kwargs[argument_name] = attrs[f"{type_name}_ids"]

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
        parent_id = origin(self).get((primary_type, primary_id))
        self._run_case(
            case,
            rule_name=rule_name,
            parent_id=parent_id,
            applied_parameters=list(bundle_args.keys()),
        )
        if track_deletion:
            # Track the deleted id's origin so use-after-delete probes can stitch parentage.
            self._deleted_id_origins[(primary_type, primary_id)] = case.id
            return primary_id
        return None

    body.__name__ = rule_name
    if target_bundle is not None:
        return rule_name, rule(target=target_bundle, data=st.data(), **bundle_kwargs)(body)
    return rule_name, rule(data=st.data(), **bundle_kwargs)(body)


def _make_cleanup_rule(
    *, field_name: str, consumed: dict[str, str], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Cleanup mutation: consumes from the alive bundle, emits into the deleted bundle."""
    primary_type = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="",
        primary_source=consumes(attrs[f"{primary_type}_ids"]),
        target_bundle=attrs[f"deleted_{primary_type}_ids"],
        origin=_alive_origin,
        track_deletion=True,
    )


def _make_double_cleanup_rule(
    *, field_name: str, consumed: dict[str, str], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Probe: re-fires the cleanup against an already-deleted id (consumed once per id)."""
    primary_type = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="_double",
        primary_source=consumes(attrs[f"deleted_{primary_type}_ids"]),
        target_bundle=None,
        origin=_deleted_origin,
        track_deletion=False,
    )


def _make_use_after_delete_rule(
    *, field_name: str, consumed: dict[str, str], root_label: str, attrs: dict[str, Any]
) -> tuple[str, Callable]:
    """Probe: re-targets a non-cleanup operation at a known-deleted id (no consume)."""
    primary_type = consumed[next(iter(consumed))]
    return _make_lifecycle_rule(
        field_name=field_name,
        root_label=root_label,
        consumed=consumed,
        attrs=attrs,
        rule_name_suffix="_on_deleted",
        primary_source=attrs[f"deleted_{primary_type}_ids"],
        target_bundle=None,
        origin=_deleted_origin,
        track_deletion=False,
    )
