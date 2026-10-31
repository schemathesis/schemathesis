"""Stateful testing for gRPC: response fields of one method fill request fields of another.

Links follow Google API conventions:

- `Create<X>` returns a resource whose `name` feeds the `name` request field of `Get<X>`, `Update<X>` and `Delete<X>`.
- A method with a `page_token` request field and a `next_page_token` response field pages through its own results.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.protobuf import message, message_factory
from google.protobuf.descriptor import FieldDescriptor
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, MultipleResults, multiple, precondition, rule

from schemathesis.core.errors import NoLinksFound
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis import add_database_key
from schemathesis.generation.meta import TestPhase
from schemathesis.generation.stateful.control import (
    BundleTransitions,
    OperationTransitions,
    TransitionEdge,
    TransitionEndpoint,
)
from schemathesis.generation.stateful.state_machine import BundleStateMachine, normalize_name, unique_rule_name
from schemathesis.specs.grpc.converter import json_fields
from schemathesis.specs.grpc.status import OK, grpc_status, starts_with_verb

if TYPE_CHECKING:
    from google.protobuf.descriptor import Descriptor

    from schemathesis.core import Body, NotSet
    from schemathesis.core.transport import Response
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.grpc.schemas import GrpcSchema

RESOURCE_CONSUMER_VERBS = ("Get", "Update", "Delete")
# A long-running create answers with an operation, whose `name` is not the resource name.
LONG_RUNNING_OPERATION = "google.longrunning.Operation"


@dataclass(frozen=True, slots=True)
class GrpcLink:
    """A request field of `target` filled with a response field of `source`."""

    source: str
    target: str
    # Field name in the source response message.
    source_field: str
    # JSON name of the target request field, as generated bodies key it.
    target_field: str


@dataclass(frozen=True, slots=True)
class Produced:
    """Values a call returned for later steps, with the request that got them."""

    case_id: str
    body: Body | NotSet
    # Source field name -> non-empty value.
    values: dict[str, str]


def infer_links(operations: Iterable[APIOperation]) -> list[GrpcLink]:
    """Links between the given unary methods, by Google API naming conventions."""
    by_label = {operation.label: operation for operation in operations}
    links = []
    for label, operation in by_label.items():
        method = operation.definition.raw
        service, _, name = label.partition("/")
        if (
            starts_with_verb(name, ("Create",))
            and method.output_type.full_name != LONG_RUNNING_OPERATION
            and _string_field(method.output_type, "name") is not None
        ):
            resource = name.removeprefix("Create")
            for verb in RESOURCE_CONSUMER_VERBS:
                target = by_label.get(f"{service}/{verb}{resource}")
                if target is None:
                    continue
                field = _string_field(target.definition.raw.input_type, "name")
                if field is not None:
                    links.append(GrpcLink(label, target.label, "name", field.json_name))
        page_token = _string_field(method.input_type, "page_token")
        if page_token is not None and _string_field(method.output_type, "next_page_token") is not None:
            links.append(GrpcLink(label, label, "next_page_token", page_token.json_name))
    return links


def _string_field(descriptor: Descriptor, name: str) -> FieldDescriptor | None:
    field = descriptor.fields_by_name.get(name)
    if field is None or field.type != FieldDescriptor.TYPE_STRING or field.is_repeated:
        return None
    return field


def response_value(operation: APIOperation, response: Response, field: str) -> str:
    """The string `field` of a successful response; empty when the call failed or the body does not decode."""
    if grpc_status(response) != OK:
        return ""
    message_class = message_factory.GetMessageClass(operation.definition.raw.output_type)
    try:
        decoded = message_class.FromString(response.content)
    except message.DecodeError:
        return ""
    return getattr(decoded, field)


def with_value(operation: APIOperation, body: Body | NotSet, field: FieldDescriptor, value: str) -> Body:
    """`body` with `field` set to `value`, and other members of its `oneof` cleared."""
    if isinstance(body, bytes):
        # A hook may have serialized the message already.
        decoded = message_factory.GetMessageClass(operation.definition.raw.input_type).FromString(body)
        setattr(decoded, field.name, value)
        return decoded.SerializeToString()
    fields = dict(body) if isinstance(body, dict) else {}
    oneof = field.containing_oneof
    if oneof is not None:
        for member in oneof.fields:
            fields.pop(member.json_name, None)
            fields.pop(member.name, None)
    fields.pop(field.name, None)
    fields[field.json_name] = value
    return fields


class GrpcStateMachine(BundleStateMachine):
    """State machine that passes resource names and page tokens between gRPC calls."""

    schema: GrpcSchema

    def _produce(self, output: StepOutput | None, fields: tuple[str, ...]) -> MultipleResults[Produced]:
        if output is None:
            return multiple()
        case = output.case
        values = {field: value for field in fields if (value := response_value(case.operation, output.response, field))}
        if not values:
            return multiple()
        return multiple(Produced(case_id=case.id, body=case.body, values=values))


def create_state_machine(schema: GrpcSchema) -> type[GrpcStateMachine]:
    operations = {operation.label: operation for operation in schema._iter_operations()}
    links = infer_links(operations.values())
    if not links:
        raise _no_links(schema)
    # Source label -> response fields its links read, in link order and without repeats.
    fields_by_source: dict[str, dict[str, None]] = {}
    for link in links:
        fields_by_source.setdefault(link.source, {})[link.source_field] = None
    produced = {label: tuple(fields) for label, fields in fields_by_source.items()}
    bundles = {label: Bundle(f"{label}.produced") for label in produced}
    transitions = BundleTransitions(
        operations={label: OperationTransitions() for label in operations},
        bundle_sources={bundle.name: [label] for label, bundle in bundles.items()},
    )
    attrs: dict[str, object] = {
        "schema": schema,
        "_transitions": transitions,
        "_root_labels": frozenset(produced),
    }
    root_rules = []
    for label, fields in produced.items():
        attrs[bundles[label].name] = bundles[label]
        name = unique_rule_name(normalize_name(label), attrs)
        attrs[name] = _root_rule(name, operations[label], fields, bundles[label])
        root_rules.append(name)
    attrs["root_rules"] = frozenset(root_rules)
    for link in links:
        edge = TransitionEdge(source=TransitionEndpoint(link.source), target=TransitionEndpoint(link.target))
        transitions.operations[link.source].outgoing.append(edge)
        transitions.operations[link.target].incoming.append(edge)
        name = unique_rule_name(normalize_name(f"{link.source}_to_{link.target}"), attrs)
        attrs[name] = _link_rule(
            name, link, operations[link.target], bundles[link.source], bundles.get(link.target), produced
        )
    machine = type("GrpcStateMachine", (GrpcStateMachine,), attrs)
    add_database_key(machine, "\n".join(attrs))
    return machine


def _no_links(schema: GrpcSchema) -> NoLinksFound:
    if infer_links(schema._iter_all_operations()):
        return NoLinksFound(
            "All methods that pass values to each other are excluded by filters",
            "Review your filters to include linked methods",
        )
    return NoLinksFound(
        "No methods pass values to each other",
        "Stateful testing passes `name` from `Create<X>` to `Get<X>`, `Update<X>` and `Delete<X>`, "
        "and `next_page_token` back into `page_token`",
    )


def _root_rule(
    rule_name: str, operation: APIOperation, fields: tuple[str, ...], target: Bundle
) -> Callable[..., MultipleResults[Produced]]:
    label = operation.label
    strategy = operation.as_strategy(phase=TestPhase.STATEFUL)

    def body(self: GrpcStateMachine, data: st.DataObject) -> MultipleResults[Produced]:
        case = data.draw(strategy)
        output = self._run_case(case, rule_name=rule_name, parent_id=None, applied_parameters=None)
        result = self._produce(output, fields)
        if result.values:
            self.control.record_productive_root(label)
        return result

    def allowed(self: GrpcStateMachine) -> bool:
        return self.control.allow_root_transition(label, self.bundles)

    body.__name__ = rule_name
    return precondition(allowed)(rule(target=target, data=st.data())(body))


def _link_rule(
    rule_name: str,
    link: GrpcLink,
    operation: APIOperation,
    source: Bundle,
    target: Bundle | None,
    produced: dict[str, tuple[str, ...]],
) -> Callable[..., MultipleResults[Produced] | None]:
    strategy = operation.as_strategy(phase=TestPhase.STATEFUL)
    field = json_fields(operation.definition.raw.input_type)[link.target_field]
    # A page token is only valid with the arguments of the request that got it.
    repeats_request = link.source == link.target

    def call(self: GrpcStateMachine, data: st.DataObject, parent: Produced) -> StepOutput | None:
        value = parent.values.get(link.source_field)
        if value is None:
            return None
        case = data.draw(strategy)
        case.body = with_value(operation, parent.body if repeats_request else case.body, field, value)
        return self._run_case(
            case,
            rule_name=rule_name,
            parent_id=parent.case_id,
            applied_parameters=[(ParameterLocation.BODY, link.target_field)],
        )

    def allowed(self: GrpcStateMachine) -> bool:
        return self.control.allow_transition(link.source, link.target)

    if target is None:

        def consume(self: GrpcStateMachine, data: st.DataObject, parent: Produced) -> None:
            call(self, data, parent)

        consume.__name__ = rule_name
        return precondition(allowed)(rule(data=st.data(), parent=source)(consume))

    def forward(self: GrpcStateMachine, data: st.DataObject, parent: Produced) -> MultipleResults[Produced]:
        return self._produce(call(self, data, parent), produced[link.target])

    forward.__name__ = rule_name
    return precondition(allowed)(rule(target=target, data=st.data(), parent=source)(forward))
