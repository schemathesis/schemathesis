import functools
import json
from typing import Any, Callable, Dict, Generator, List, Optional

Generated = Dict[str, Any]
Definition = Dict[str, Any]
DefinitionList = List[Definition]
MapFunction = Callable[[Generated], Generated]


def compose(*functions: Callable) -> Callable:
    """Compose multiple functions into a single one."""

    def noop(x: Any) -> Any:
        return x

    return functools.reduce(lambda f, g: lambda x: f(g(x)), functions, noop)


def make_serializer(
    func: Callable[[DefinitionList], Generator[Optional[Callable], None, None]]
) -> Callable[[DefinitionList], Optional[Callable]]:
    """A maker function to avoid code duplication."""

    def _wrapper(definitions: DefinitionList) -> Optional[Callable]:
        conversions = list(func(definitions))
        if conversions:
            return compose(*[conv for conv in conversions if conv is not None])
        return None

    return _wrapper


def _serialize_openapi3(definitions: DefinitionList) -> Generator[Optional[Callable], None, None]:
    """Different collection styles for Open API 3.0."""
    for definition in definitions:
        name = definition["name"]
        if "content" in definition:
            # https://swagger.io/docs/specification/describing-parameters/#schema-vs-content
            options = iter(definition["content"].keys())
            media_type = next(options, None)
            if media_type == "application/json":
                yield to_json(name)
        else:
            # Simple serialization
            style = definition.get("style")
            explode = definition.get("explode")
            type_ = definition.get("schema", {}).get("type")
            if definition["in"] == "path":
                yield from _serialize_path_openapi3(name, type_, style, explode)
            elif definition["in"] == "query":
                yield from _serialize_query_openapi3(name, type_, style, explode)
            elif definition["in"] == "header":
                yield from _serialize_header_openapi3(name, type_, explode)
            elif definition["in"] == "cookie":
                yield from _serialize_cookie_openapi3(name, type_, explode)


def _serialize_path_openapi3(
    name: str, type_: str, style: Optional[str], explode: Optional[bool]
) -> Generator[Optional[Callable], None, None]:
    # pylint: disable=too-many-branches
    if style == "simple":
        if type_ == "object":
            if explode is False:
                yield comma_delimited_object(name)
            if explode is True:
                yield delimited_object(name)
        if type_ == "array":
            yield delimited(name, delimiter=",")
    if style == "label":
        if type_ == "object":
            yield label_object(name, explode=explode)
        elif type_ == "array":
            yield label_array(name, explode=explode)
        else:
            yield label_primitive(name)
    if style == "matrix":
        if type_ == "object":
            yield matrix_object(name, explode=explode)
        elif type_ == "array":
            yield matrix_array(name, explode=explode)
        else:
            yield matrix_primitive(name)


def _serialize_query_openapi3(
    name: str, type_: str, style: Optional[str], explode: Optional[bool]
) -> Generator[Optional[Callable], None, None]:
    if type_ == "object":
        if style == "deepObject":
            yield deep_object(name)
        if style is None or style == "form":
            if explode is False:
                yield comma_delimited_object(name)
            if explode is True:
                yield extracted_object(name)
    elif type_ == "array" and explode is False:
        if style == "pipeDelimited":
            yield delimited(name, delimiter="|")
        if style == "spaceDelimited":
            yield delimited(name, delimiter=" ")
        if style is None or style == "form":  # "form" is the default style
            yield delimited(name, delimiter=",")


def _serialize_header_openapi3(
    name: str, type_: str, explode: Optional[bool]
) -> Generator[Optional[Callable], None, None]:
    # Header parameters always use the "simple" style, that is, comma-separated values
    if type_ == "array":
        yield delimited(name, delimiter=",")
    if type_ == "object":
        if explode is False:
            yield comma_delimited_object(name)
        if explode is True:
            yield delimited_object(name)


def _serialize_cookie_openapi3(
    name: str, type_: str, explode: Optional[bool]
) -> Generator[Optional[Callable], None, None]:
    # Cookie parameters always use the "form" style
    if explode and type_ in ("array", "object"):
        # `explode=true` doesn't make sense
        # I.e. we can't create multiple values for the same cookie
        # We use the same behavior as in the examples - https://swagger.io/docs/specification/serialization/
        # The item is removed
        yield nothing(name)
    if explode is False:
        if type_ == "array":
            yield delimited(name, delimiter=",")
        if type_ == "object":
            yield comma_delimited_object(name)


def _serialize_swagger2(definitions: DefinitionList) -> Generator[Optional[Callable], None, None]:
    """Different collection formats for Open API 2.0."""
    for definition in definitions:
        name = definition["name"]
        collection_format = definition.get("collectionFormat", "csv")
        type_ = definition.get("type")
        if definition["in"] != "body" and type_ in ("array", "object"):
            if collection_format == "csv":
                yield delimited(name, delimiter=",")
            if collection_format == "ssv":
                yield delimited(name, delimiter=" ")
            if collection_format == "tsv":
                yield delimited(name, delimiter="\t")
            if collection_format == "pipes":
                yield delimited(name, delimiter="|")


serialize_openapi3_parameters = make_serializer(_serialize_openapi3)
serialize_swagger2_parameters = make_serializer(_serialize_swagger2)


def conversion(func: Callable[..., None]) -> Callable:
    def _wrapper(name: str, **kwargs: Any) -> MapFunction:
        def _map(item: Generated) -> Generated:
            if name in item:
                func(item, name, **kwargs)
            return item

        return _map

    return _wrapper


def make_delimited(data: Dict[str, Any], delimiter: str = ",") -> str:
    return delimiter.join(f"{key}={value}" for key, value in data.items())


@conversion
def to_json(item: Generated, name: str) -> None:
    """Serialize an item to JSON."""
    item[name] = json.dumps(item[name])


@conversion
def delimited(item: Generated, name: str, delimiter: str) -> None:
    item[name] = delimiter.join(map(str, item[name]))


@conversion
def deep_object(item: Generated, name: str) -> None:
    """Serialize an object with `deepObject` style.

    id={"role": "admin", "firstName": "Alex"} => id[role]=admin&id[firstName]=Alex
    """
    generated = item.pop(name)
    item.update({f"{name}[{key}]": value for key, value in generated.items()})


@conversion
def comma_delimited_object(item: Generated, name: str) -> None:
    item[name] = ",".join(map(str, sum(item[name].items(), ())))


@conversion
def delimited_object(item: Generated, name: str) -> None:
    item[name] = make_delimited(item[name])


@conversion
def extracted_object(item: Generated, name: str) -> None:
    """Merge a child node to to the parent one."""
    generated = item.pop(name)
    item.update(generated)


@conversion
def label_primitive(item: Generated, name: str) -> None:
    """Serialize a primitive value with the `label` style.

    5 => ".5"
    """
    item[name] = f".{item[name]}"


@conversion
def label_array(item: Generated, name: str, explode: Optional[bool]) -> None:
    """Serialize an array with the `label` style.

    Explode=True

        id=[3, 4, 5] => ".3.4.5"

    Explode=False

        id=[3, 4, 5] => ".3,4,5"
    """
    if explode:
        delimiter = "."
    else:
        delimiter = ","
    item[name] = f".{delimiter.join(map(str, item[name]))}"


@conversion
def label_object(item: Generated, name: str, explode: Optional[bool]) -> None:
    """Serialize an object with the `label` style.

    Explode=True

        id={"role": "admin", "firstName": "Alex"} => ".role=admin.firstName=Alex"

    Explode=False

        id={"role": "admin", "firstName": "Alex"} => ".role=admin,firstName,Alex"
    """
    if explode:
        new = make_delimited(item[name], ".")
    else:
        object_items = map(str, sum(item[name].items(), ()))
        new = ",".join(object_items)
    item[name] = f".{new}"


@conversion
def matrix_primitive(item: Generated, name: str) -> None:
    """Serialize a primitive value with the `matrix` style.

    5 => ";id=5"
    """
    item[name] = f";{name}={item[name]}"


@conversion
def matrix_array(item: Generated, name: str, explode: Optional[bool]) -> None:
    """Serialize an array with the `matrix` style.

    Explode=True

        id=[3, 4, 5] => ";id=3;id=4;id=5"

    Explode=False

        id=[3, 4, 5] => ";id=3,4,5"
    """
    if explode:
        new = ";".join(f"{name}={value}" for value in item[name])
    else:
        new = ",".join(map(str, item[name]))
    item[name] = f";{new}"


@conversion
def matrix_object(item: Generated, name: str, explode: Optional[bool]) -> None:
    """Serialize an object with the `matrix` style.

    Explode=True

        id={"role": "admin", "firstName": "Alex"} => ";role=admin;firstName=Alex"

    Explode=False

        id={"role": "admin", "firstName": "Alex"} => ";role=admin,firstName,Alex"
    """
    if explode:
        new = make_delimited(item[name], ";")
    else:
        object_items = map(str, sum(item[name].items(), ()))
        new = ",".join(object_items)
    item[name] = f";{new}"


@conversion
def nothing(item: Generated, name: str) -> None:
    """Remove a key from an item."""
    item.pop(name, None)
