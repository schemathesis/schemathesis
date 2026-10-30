from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

from google.protobuf import message_factory
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message

if TYPE_CHECKING:
    from google.protobuf.descriptor import OneofDescriptor
    from google.protobuf.descriptor_pool import DescriptorPool

# Option extensions holding validation rules: `buf.validate` first, then the legacy `validate` package.
FIELD_EXTENSIONS: Final = ("buf.validate.field", "validate.rules")
ONEOF_EXTENSIONS: Final = ("buf.validate.oneof", "validate.required")
# `buf.validate.Ignore` values that skip the rules for a zero value, and that skip them always.
IGNORE_IF_ZERO_VALUE: Final = frozenset({1, 2})
IGNORE_ALWAYS: Final = 3

RuleValue: TypeAlias = "bool | int | float | str | bytes | list[RuleValue] | FieldConstraints | Message"


@dataclass(slots=True, frozen=True)
class FieldConstraints:
    """Validation rules declared on one field."""

    # Rule group for the field's values, named as in the rules message: `string`, `int64`, `repeated`, `map`, ...
    kind: str | None
    # Rules of that group by their proto name; `items`, `keys` and `values` hold nested constraints.
    rules: dict[str, RuleValue]
    # The field must be set; for a field without presence, to a non-zero value.
    required: bool
    # The rules hold only for non-zero values.
    ignore_empty: bool


class ConstraintReader:
    """Reads validation rules from the options of descriptors in one pool."""

    __slots__ = ("_field_options", "_oneof_options")

    def __init__(self, pool: DescriptorPool) -> None:
        # Options parsed with the pool's own classes expose the rule extensions the pool defines.
        self._field_options = message_factory.GetMessageClass(
            pool.FindMessageTypeByName("google.protobuf.FieldOptions")
        )
        self._oneof_options = message_factory.GetMessageClass(
            pool.FindMessageTypeByName("google.protobuf.OneofOptions")
        )

    @classmethod
    def from_pool(cls, pool: DescriptorPool) -> ConstraintReader | None:
        """A reader for `pool`; `None` when the pool defines no validation rules."""
        for name in FIELD_EXTENSIONS:
            try:
                pool.FindExtensionByName(name)
            except KeyError:
                continue
            return cls(pool)
        return None

    def field(self, field: FieldDescriptor) -> FieldConstraints | None:
        extensions = _extensions(self._field_options, field)
        for name in FIELD_EXTENSIONS:
            rules = extensions.get(name)
            if isinstance(rules, Message):
                return _constraints(rules)
        return None

    def oneof_required(self, oneof: OneofDescriptor) -> bool:
        extensions = _extensions(self._oneof_options, oneof)
        for name in ONEOF_EXTENSIONS:
            if name in extensions:
                value = extensions[name]
                # The legacy package sets a plain flag, `buf.validate` a rules message.
                if isinstance(value, bool):
                    return value
                return bool(_group(value).get("required"))
        return False


def _extensions(
    options_class: type[Message], descriptor: FieldDescriptor | OneofDescriptor
) -> dict[str, Message | bool]:
    """Extension values set on the options of `descriptor`, by extension name."""
    if not descriptor.has_options:
        return {}
    options = options_class.FromString(descriptor.GetOptions().SerializeToString())
    return {field.full_name: value for field, value in options.ListFields() if field.is_extension}


def _constraints(rules: Message) -> FieldConstraints | None:
    kind = None
    group: dict[str, RuleValue] = {}
    required = False
    ignore = 0
    for descriptor, value in rules.ListFields():
        oneof = descriptor.containing_oneof
        if oneof is not None and oneof.name == "type":
            kind = descriptor.name
            group = _group(value, rules.DESCRIPTOR.full_name)
            # The legacy package marks timestamp, duration and `Any` fields as required inside their group.
            required = required or bool(group.pop("required", False))
        elif descriptor.name == "required":
            required = required or value
        elif descriptor.name == "ignore":
            ignore = value
        elif descriptor.name == "message":
            # The legacy package marks message fields as required in a separate group.
            required = required or bool(_group(value).get("required"))
    if ignore == IGNORE_ALWAYS:
        return None
    ignore_empty = ignore in IGNORE_IF_ZERO_VALUE or bool(group.pop("ignore_empty", False))
    return FieldConstraints(kind=kind, rules=group, required=required, ignore_empty=ignore_empty)


def _group(rules: Message, field_rules: str = "") -> dict[str, RuleValue]:
    """Rules of one group; `items`, `keys` and `values` hold nested `field_rules` messages."""
    group: dict[str, RuleValue] = {}
    for descriptor, value in rules.ListFields():
        if descriptor.message_type is not None and descriptor.message_type.full_name == field_rules:
            nested = _constraints(value)
            if nested is not None:
                group[descriptor.name] = nested
        elif descriptor.is_repeated:
            group[descriptor.name] = list(value)
        else:
            group[descriptor.name] = value
    return group
