from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, overload
from urllib.request import urlopen

import jsonschema
import requests
import yaml

from ...constants import DEFAULT_RESPONSE_TIMEOUT
from ...utils import StringDatesYAMLLoader, fast_deepcopy
from .constants import ALL_KEYWORDS
from .converter import to_json_schema_recursive
from .utils import get_type

# Reference resolving will stop after this depth
RECURSION_DEPTH_LIMIT = 100


def load_file_impl(location: str, opener: Callable) -> Dict[str, Any]:
    """Load a schema from the given file."""
    with opener(location) as fd:
        return yaml.load(fd, StringDatesYAMLLoader)


@lru_cache()
def load_file(location: str) -> Dict[str, Any]:
    """Load a schema from the given file."""
    return load_file_impl(location, open)


@lru_cache()
def load_file_uri(location: str) -> Dict[str, Any]:
    """Load a schema from the given file uri."""
    return load_file_impl(location, urlopen)


def load_remote_uri(uri: str) -> Any:
    """Load the resource and parse it as YAML / JSON."""
    response = requests.get(uri, timeout=DEFAULT_RESPONSE_TIMEOUT / 1000)
    return yaml.load(response.content, StringDatesYAMLLoader)


JSONType = Union[None, bool, float, str, list, Dict[str, Any]]


class InliningResolver(jsonschema.RefResolver):
    """Inlines resolved schemas."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "handlers", {"file": load_file_uri, "": load_file, "http": load_remote_uri, "https": load_remote_uri}
        )
        super().__init__(*args, **kwargs)

    @overload  # pragma: no mutate
    def resolve_all(self, item: Dict[str, Any], recursion_level: int = 0) -> Dict[str, Any]:
        pass

    @overload  # pragma: no mutate
    def resolve_all(self, item: List, recursion_level: int = 0) -> List:
        pass

    def resolve_all(self, item: JSONType, recursion_level: int = 0) -> JSONType:
        """Recursively resolve all references in the given object."""
        if isinstance(item, dict):
            ref = item.get("$ref")
            if ref is not None and isinstance(ref, str):
                with self.resolving(ref) as resolved:
                    # If the next level of recursion exceeds the limit, then we need to copy it explicitly
                    # In other cases, this method create new objects for mutable types (dict & list)
                    next_recursion_level = recursion_level + 1
                    if next_recursion_level > RECURSION_DEPTH_LIMIT:
                        copied = fast_deepcopy(resolved)
                        remove_optional_references(copied)
                        return copied
                    return self.resolve_all(resolved, next_recursion_level)
            return {key: self.resolve_all(sub_item, recursion_level) for key, sub_item in item.items()}
        if isinstance(item, list):
            return [self.resolve_all(sub_item, recursion_level) for sub_item in item]
        return item

    def resolve_in_scope(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Dict[str, Any]]:
        scopes = [scope]
        # if there is `$ref` then we have a scope change that should be used during validation later to
        # resolve nested references correctly
        if "$ref" in definition:
            self.push_scope(scope)
            try:
                new_scope, definition = fast_deepcopy(self.resolve(definition["$ref"]))
            finally:
                self.pop_scope()
            scopes.append(new_scope)
        return scopes, definition


class ConvertingResolver(InliningResolver):
    """Convert resolved OpenAPI schemas to JSON Schema.

    When recursive schemas are validated we need to have resolved documents properly converted.
    This approach is the simplest one, since this logic isolated in a single place.
    """

    def __init__(self, *args: Any, nullable_name: Any, is_response_schema: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.nullable_name = nullable_name
        self.is_response_schema = is_response_schema

    def resolve(self, ref: str) -> Tuple[str, Any]:
        url, document = super().resolve(ref)
        document = to_json_schema_recursive(
            document, nullable_name=self.nullable_name, is_response_schema=self.is_response_schema
        )
        return url, document


def remove_optional_references(schema: Dict[str, Any]) -> None:
    """Remove optional parts of the schema that contain references.

    It covers only the most popular cases, as removing all optional parts is complicated.
    We might fall back to filtering out invalid cases in the future.
    """

    def clean_properties(s: Dict[str, Any]) -> None:
        properties = s["properties"]
        required = s.get("required", [])
        for name, value in list(properties.items()):
            if name not in required and contains_ref(value):
                # Drop the property - it will not be generated
                del properties[name]
            elif on_single_item_combinators(value):
                properties.pop(name, None)
            else:
                stack.append(value)

    def clean_items(s: Dict[str, Any]) -> None:
        items = s["items"]
        min_items = s.get("minItems", 0)
        if not min_items:
            if isinstance(items, dict) and ("$ref" in items or on_single_item_combinators(items)):
                force_empty_list(s)
            if isinstance(items, list) and any_ref(items):
                force_empty_list(s)

    def clean_additional_properties(s: Dict[str, Any]) -> None:
        additional_properties = s["additionalProperties"]
        if isinstance(additional_properties, dict) and "$ref" in additional_properties:
            s["additionalProperties"] = False

    def force_empty_list(s: Dict[str, Any]) -> None:
        del s["items"]
        s["maxItems"] = 0

    def any_ref(i: List[Dict[str, Any]]) -> bool:
        return any("$ref" in item for item in i)

    def contains_ref(s: Dict[str, Any]) -> bool:
        if "$ref" in s:
            return True
        i = s.get("items")
        return (isinstance(i, dict) and "$ref" in i) or isinstance(i, list) and any_ref(i)

    def can_elide(s: Dict[str, Any]) -> bool:
        # Whether this schema could be dropped from a list of schemas
        type_ = get_type(s)
        if type_ == ["object"]:
            # Empty object is valid for this schema -> could be dropped
            return s.get("required", []) == [] and s.get("minProperties", 0) == 0
        # Has at least one keyword -> should not be removed
        return not any(k in ALL_KEYWORDS for k in s)

    def on_single_item_combinators(s: Dict[str, Any]) -> List[str]:
        # Schema example:
        # {
        #     "type": "object",
        #     "properties": {
        #         "parent": {
        #             "allOf": [{"$ref": "#/components/schemas/User"}]
        #         }
        #     }
        # }
        found = []
        for keyword in ("allOf", "oneOf", "anyOf"):
            v = s.get(keyword)
            if v is not None:
                elided = [sub for sub in v if not can_elide(sub)]
                if len(elided) == 1 and "$ref" in elided[0]:
                    found.append(keyword)
        return found

    stack = [schema]
    while stack:
        definition = stack.pop()
        # Optional properties
        if "properties" in definition:
            clean_properties(definition)
        # Optional items
        if "items" in definition:
            clean_items(definition)
        # Not required additional properties
        if "additionalProperties" in definition:
            clean_additional_properties(definition)
        for k in on_single_item_combinators(definition):
            del definition[k]


def resolve_pointer(document: Any, pointer: str) -> Optional[Union[Dict, List, str, int, float]]:
    """Implementation is adapted from Rust's `serde-json` crate.

    Ref: https://github.com/serde-rs/json/blob/master/src/value/mod.rs#L751
    """
    if not pointer:
        return document
    if not pointer.startswith("/"):
        return None

    def replace(value: str) -> str:
        return value.replace("~1", "/").replace("~0", "~")

    tokens = map(replace, pointer.split("/")[1:])
    target = document
    for token in tokens:
        if isinstance(target, dict):
            target = target.get(token)
        elif isinstance(target, list):
            try:
                target = target[int(token)]
            except IndexError:
                return None
        else:
            return None
    return target
