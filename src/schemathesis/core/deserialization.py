from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any, BinaryIO, TextIO

if TYPE_CHECKING:
    import yaml


@lru_cache
def get_yaml_loader() -> type[yaml.SafeLoader]:
    """Create a YAML loader, that doesn't parse specific tokens into Python objects."""
    import yaml

    try:
        from yaml import CSafeLoader as SafeLoader
    except ImportError:
        from yaml import SafeLoader  # type: ignore

    cls: type[yaml.SafeLoader] = type("YAMLLoader", (SafeLoader,), {})
    cls.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in mapping if tag != "tag:yaml.org,2002:timestamp"]
        for key, mapping in cls.yaml_implicit_resolvers.copy().items()
    }

    # Fix pyyaml scientific notation parse bug
    # See PR: https://github.com/yaml/pyyaml/pull/174 for upstream fix
    cls.add_implicit_resolver(  # type: ignore
        "tag:yaml.org,2002:float",
        re.compile(
            r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                       |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                       |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                       |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                       |[-+]?\.(?:inf|Inf|INF)
                       |\.(?:nan|NaN|NAN))$""",
            re.VERBOSE,
        ),
        list("-+0123456789."),
    )

    def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> dict[str, Any]:
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)  # type: ignore
        mapping = {}
        for key_node, value_node in node.value:
            # If the key has a tag different from `str` - use its string value.
            # With this change all integer keys or YAML 1.1 boolean-ish values like "on" / "off" will not be cast to
            # a different type
            if key_node.tag != "tag:yaml.org,2002:str":
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep)  # type: ignore
            mapping[key] = self.construct_object(value_node, deep)  # type: ignore
        return mapping

    cls.construct_mapping = construct_mapping  # type: ignore
    return cls


def deserialize_yaml(stream: str | bytes | TextIO | BinaryIO) -> Any:
    import yaml

    return yaml.load(stream, get_yaml_loader())
