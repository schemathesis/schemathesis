from copy import deepcopy
from functools import lru_cache
from typing import Any, Callable, Dict, List, Tuple, Union, overload
from urllib.request import urlopen

import jsonschema
import requests
import yaml

from ...utils import StringDatesYAMLLoader
from .converter import to_json_schema_recursive

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
    response = requests.get(uri)
    return yaml.load(response.content, StringDatesYAMLLoader)


class InliningResolver(jsonschema.RefResolver):
    """Inlines resolved schemas."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "handlers", {"file": load_file_uri, "": load_file, "http": load_remote_uri, "https": load_remote_uri}
        )
        super().__init__(*args, **kwargs)

    @overload  # pragma: no mutate
    def resolve_all(
        self, item: Dict[str, Any], recursion_level: int = 0
    ) -> Dict[str, Any]:  # pylint: disable=function-redefined
        pass

    @overload  # pragma: no mutate
    def resolve_all(self, item: List, recursion_level: int = 0) -> List:  # pylint: disable=function-redefined
        pass

    # pylint: disable=function-redefined
    def resolve_all(self, item: Union[Dict[str, Any], List], recursion_level: int = 0) -> Union[Dict[str, Any], List]:
        """Recursively resolve all references in the given object."""
        if recursion_level > RECURSION_DEPTH_LIMIT:
            return item
        if isinstance(item, dict):
            ref = item.get("$ref")
            if ref is not None and isinstance(ref, str):
                with self.resolving(ref) as resolved:
                    return self.resolve_all(resolved, recursion_level + 1)
            item = deepcopy(item)
            for key, sub_item in item.items():
                item[key] = self.resolve_all(sub_item, recursion_level)
        elif isinstance(item, list):
            item = [self.resolve_all(sub_item, recursion_level) for sub_item in deepcopy(item)]
        return item

    def resolve_in_scope(self, definition: Dict[str, Any], scope: str) -> Tuple[List[str], Dict[str, Any]]:
        scopes = [scope]
        # if there is `$ref` then we have a scope change that should be used during validation later to
        # resolve nested references correctly
        if "$ref" in definition:
            with self.in_scope(scope):
                new_scope, definition = deepcopy(self.resolve(definition["$ref"]))
            scopes.append(new_scope)
        return scopes, definition


class ConvertingResolver(InliningResolver):
    """Convert resolved OpenAPI schemas to JSON Schema.

    When recursive schemas are validated we need to have resolved documents properly converted.
    This approach is the simplest one, since this logic isolated in a single place.
    """

    def __init__(self, *args: Any, nullable_name: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.nullable_name = nullable_name

    def resolve(self, ref: str) -> Tuple[str, Any]:
        url, document = super().resolve(ref)
        document = to_json_schema_recursive(document, nullable_name=self.nullable_name)
        return url, document
