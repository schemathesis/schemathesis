from ....internal.copy import fast_deepcopy, merge_into
from .iteration import iter_subschemas
from .keys import _key_for_reference
from .types import MovedSchemas, ObjectSchema


def inline_recursive_references(referenced_schemas: MovedSchemas, recursive: set[str]) -> None:
    keys = {_key_for_reference(ref)[0] for ref in recursive}
    originals = {key: fast_deepcopy(value) if key in keys else value for key, value in referenced_schemas.items()}
    for reference in recursive:
        key, _ = _key_for_reference(reference)
        _inline_recursive_references(referenced_schemas[key], originals, recursive, [key])


def _inline_recursive_references(
    schema: ObjectSchema, referenced_schemas: MovedSchemas, recursive: set[str], path: list[str]
) -> None:
    """Inline all recursive references in the given item."""
    reference = schema.get("$ref")
    if isinstance(reference, str):
        # TODO: There could be less traversal if we know where refs are located within `refrenced_item`.
        #       Just copy the value and directly jump to the next ref in it, or iterate over them
        if reference in recursive:
            schema.clear()
            key, _ = _key_for_reference(reference)
            if path.count(key) < 3:
                referenced_item = referenced_schemas[key]
                # Extend with a deep copy as the tree should grow with owned data
                merge_into(schema, referenced_item)
                path.append(key)
                _inline_recursive_references(schema, referenced_schemas, recursive, path)
                path.pop()
        return
    for subschema in iter_subschemas(schema):
        _inline_recursive_references(subschema, referenced_schemas, recursive, path)
