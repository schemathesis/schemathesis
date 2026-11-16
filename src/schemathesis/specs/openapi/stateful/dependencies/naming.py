from __future__ import annotations

import enum
import re
from collections.abc import Collection, Mapping
from functools import lru_cache
from typing import TypeAlias

from schemathesis.core.text import to_pascal_case

# Prefixes like `item_id` name no resource type, so these parameters take the resource from the path
GENERIC_PREFIXES = frozenset({"item", "resource", "object", "entity"})


@lru_cache(maxsize=512)
def strip_version_prefix(path: str) -> str:
    """Drop leading `/api` and `/vN` segments: `/api/v2/orders` -> `/orders`, `/oauth2/files` stays."""
    if not path:
        return path
    segments = path.split("/")
    # Skip the empty segment before the leading `/`
    start = 1
    while start < len(segments):
        seg = segments[start]
        if seg == "api" or _is_version_segment(seg):
            start += 1
        else:
            break
    return "/" + "/".join(segments[start:])


def _is_version_segment(segment: str) -> bool:
    return len(segment) > 1 and segment[0] == "v" and segment[1:].isdigit()


def _resource_segments(path: str) -> list[str]:
    # Version segments name no resource wherever they appear (`/books/v1/{title}` is a book collection).
    return [s for s in path.split("/") if s and not _is_version_segment(s)]


class KeyKind(enum.Enum):
    """How a parameter refers to a resource."""

    # `name`, `DomainName`, `username`, `title`
    NAME = enum.auto()
    # `id`, `user_id`, `DomainSid`, `clusterArn`, `role-id`
    IDENTIFIER = enum.auto()


_IDENTIFIER_SUFFIXES = ("id", "ids", "uuid", "uuids", "guid", "guids", "sid", "arn", "arns")


@lru_cache(maxsize=2048)
def key_kind(parameter: str) -> KeyKind | None:
    lower = parameter.lower()
    if lower.endswith("name") or lower == "title":
        return KeyKind.NAME
    for suffix in _IDENTIFIER_SUFFIXES:
        if lower == suffix:
            return KeyKind.IDENTIFIER
        start = len(parameter) - len(suffix)
        # The suffix must be a separate word: `user_id`, `userId`, `UserID`, but not `paid`.
        if start > 0 and lower.endswith(suffix) and (parameter[start - 1] in "_-" or parameter[start].isupper()):
            return KeyKind.IDENTIFIER
    return None


def trailing_path_parameter(path: str) -> str | None:
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last.startswith("{") and last.endswith("}"):
        return last[1:-1]
    return None


def collection_path(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[0]


def referenced_values(
    path: str,
    path_parameters: Mapping[str, object],
    query: Mapping[str, object],
    body: object,
    created_fields: Collection[str],
) -> set[str]:
    """Values a request sent to name existing objects, rather than the one it creates."""
    # A write creates what its trailing path parameter, its created body keys and its own `id` name.
    created = trailing_path_parameter(path)
    referenced = [value for name, value in path_parameters.items() if name != created]
    referenced.extend(query.values())
    if isinstance(body, Mapping):
        referenced.extend(
            value
            for name, value in body.items()
            if name not in created_fields and name != "id" and not isinstance(value, (dict, list))
        )
    return {str(value) for value in referenced}


@lru_cache(maxsize=2048)
def from_parameter(parameter: str, path: str, *, body_field: bool = False, query: bool = False) -> str | None:
    parameter = parameter.strip()
    lower = parameter.lower()

    if lower in ("id", "ids"):
        return from_path(path, parameter_name=parameter)

    # Case-sensitive, plurals first so array fields like `siteIds` resolve before the singular suffixes
    capital_suffixes = ("Ids", "Uuids", "Guids", "Id", "Uuid", "Guid", "Arn", "Address")
    for suffix in capital_suffixes:
        if parameter.endswith(suffix):
            prefix = parameter[: -len(suffix)]
            if len(prefix) >= 2:
                # A generic prefix (`itemId`) on a path parameter takes the resource from the path
                if prefix.lower() in GENERIC_PREFIXES and f"{{{parameter}}}" in path:
                    return from_path(path, parameter_name=parameter)
                return to_pascal_case(prefix)

    # Query `projectName` -> `Project`. Path parameters and body fields keep the rules below.
    if query and parameter.endswith("Name") and len(parameter) >= 6:
        return to_pascal_case(parameter[:-4])

    # Case-insensitive, composite suffixes before shorter ones
    snake_suffixes = (
        "_id_or_slug",
        "-id-or-slug",
        "_uuids",
        "_guids",
        "_ids",
        "-uuids",
        "-guids",
        "-ids",
        "_guid",
        "_uuid",
        "_id",
        "_pk",
        "_slug",
        "_name",
        "_arn",
        "_address",
        "-guid",
        "-uuid",
        "-id",
        "-slug",
        "-name",
        "-arn",
        "-address",
    )
    for suffix in snake_suffixes:
        if lower.endswith(suffix):
            prefix = parameter[: -len(suffix)]
            if len(prefix) >= 2:
                # A generic prefix (`item_id`) on a path parameter takes the resource from the path
                if prefix.lower() in GENERIC_PREFIXES and f"{{{parameter}}}" in path:
                    return from_path(path, parameter_name=parameter)
                return to_pascal_case(prefix)

    # Case-sensitive `Sid` suffix
    if parameter.endswith("Sid"):
        prefix = parameter[:-3]
        if len(prefix) >= 2:
            return to_pascal_case(prefix)

    # Bare identifiers like `{name}` take the resource from the path, minus version prefixes (`/v1/{name}` is no `V1`)
    BARE_IDENTIFIER_NAMES = (
        "identifier",
        "uuid",
        "guid",
        "pk",
        "uid",
        "slug",
        "name",
        "username",
        "namespace",
        "token",
        "tag",
        "uri",
        "address",
        "arn",
    )
    if lower in BARE_IDENTIFIER_NAMES and f"{{{parameter}}}" in path:
        return from_path(strip_version_prefix(path), parameter_name=parameter)

    # `userid` splits only when its prefix names the path's resource; body fields need no `{parameter}` in the path
    if body_field or f"{{{parameter}}}" in path:
        prefix_lower = _concatenated_prefix(parameter, path, body_field=body_field)
        if prefix_lower is not None:
            path_resource = from_path(strip_version_prefix(path), parameter_name=parameter)
            if path_resource is not None and path_resource.lower() == prefix_lower:
                return path_resource

    # `/sessions/{session}` -> `Session` when the name equals the singular owning segment, unlike `/users/{session}`
    if f"{{{parameter}}}" in path:
        path_resource = from_path(strip_version_prefix(path), parameter_name=parameter)
        if path_resource is not None and normalize_for_matching(path_resource) == normalize_for_matching(parameter):
            return path_resource

    return None


def _concatenated_prefix(parameter: str, path: str, *, body_field: bool) -> str | None:
    lower = parameter.lower()
    # `pk` only for a trailing path parameter (`/entry/{entrypk}`); a body `serverpk` often holds a public key
    primary_key = ("pk",) if trailing_path_parameter(path) == parameter and not body_field else ()
    for suffix in ("uuid", "guid", "slug", "name", "id", *primary_key):
        if lower.endswith(suffix) and len(lower) > len(suffix):
            prefix_lower = lower[: -len(suffix)]
            return prefix_lower if len(prefix_lower) >= 2 else None
    return None


def is_concatenated_primary_key(parameter: str) -> bool:
    """Whether `parameter` joins a resource name and `pk` without a separator (`entrypk`)."""
    return _split_parameter_name(parameter)[1] == "pk"


@lru_cache(maxsize=512)
def from_path(path: str, parameter_name: str | None = None) -> str | None:
    """Resource owning `{parameter_name}`, else the singular last static segment (`/users/me` -> `User`)."""
    segments = _resource_segments(path)

    if not segments:
        return None

    if parameter_name:
        owner = _owning_segment_resource(segments, parameter_name)
        if owner is not None:
            return owner

    non_param_segments = [s for s in segments if "{" not in s]
    if non_param_segments:
        last_segment = non_param_segments[-1]
        # `self`, `me` and `current` name an instance of the parent collection
        if last_segment.lower() in ("self", "me", "current") and len(non_param_segments) > 1:
            last_segment = non_param_segments[-2]
        singular = to_singular(last_segment)
        return to_pascal_case(singular)

    return None


@lru_cache(maxsize=512)
def owning_resource(parameter: str, path: str) -> str | None:
    """Resource named by the static segment right before `{parameter}` (`/api/projects/{code}` -> `Project`)."""
    return _owning_segment_resource(_resource_segments(strip_version_prefix(path)), parameter)


@lru_cache(maxsize=1024)
def named_collection(path: str, resource: str) -> str | None:
    """`path` up to its last segment naming `resource` items, parameters blanked: `/kits/{}/options` for `Option`."""
    segments = ["{}" if "{" in segment else segment for segment in path.split("/") if segment]
    for index in range(len(segments) - 1, -1, -1):
        if segments[index] != "{}" and names_items_of(segments[index], resource):
            return "/" + "/".join(segments[: index + 1])
    return None


def names_items_of(word: str, resource: str) -> bool:
    """Whether `word` names `resource` items, alone or as a collection: `option` or `options` for `Option`."""
    return normalize_for_matching(to_singular(word)) == normalize_for_matching(resource)


def item_collections(paths: Collection[str]) -> frozenset[str]:
    """Collections a path keys items of, with blanked parameters (`/options/{id}/entry/{pk}` -> `options/{}/entry`)."""
    collections = set()
    for path in paths:
        segments = _blank_parameters(path)
        for index, segment in enumerate(segments):
            if segment == "{}":
                collections.add("/".join(segments[:index]))
    return frozenset(collections)


@lru_cache(maxsize=512)
def nested_resource_names(parameter: str, path: str, collections: frozenset[str]) -> tuple[str, ...]:
    """Item collections under `{parameter}`, normalized (`/options/{id}/entry/{entrypk}` -> `entry` for `id`)."""
    segments = _resource_segments(path)
    placeholder = f"{{{parameter}}}"
    if placeholder not in segments:
        return ()
    blanked = _blank_parameters(path)
    return tuple(
        normalize_for_matching(to_singular(segments[index]))
        for index in range(segments.index(placeholder) + 1, len(segments))
        if "/".join(blanked[: index + 1]) in collections
    )


def collection_words(path: str, collections: frozenset[str]) -> tuple[str, ...]:
    """Fixed segments naming the collection `path` lists or keys an item of, nearest first.

    `download`, `share` for `/shares/downloads/{id}`.
    """
    segments = _blank_parameters(path)
    if segments and segments[-1] == "{}":
        segments.pop()
    elif "/".join(segments) not in collections:
        return ()
    words = []
    while segments and segments[-1] != "{}":
        words.append(normalize_for_matching(to_singular(segments.pop())))
    return tuple(words)


def _blank_parameters(path: str) -> list[str]:
    return ["{}" if "{" in segment else segment for segment in _resource_segments(path)]


def _owning_segment_resource(segments: list[str], parameter: str) -> str | None:
    placeholder = f"{{{parameter}}}"
    if placeholder not in segments:
        return None
    index = segments.index(placeholder)
    if index == 0 or "{" in segments[index - 1]:
        return None
    return to_pascal_case(to_singular(segments[index - 1]))


# Static segments of a path, with `{}` for every parameter: `("sites", "{}", "managed", "devices")`.
CollectionShape: TypeAlias = tuple[str, ...]


@lru_cache(maxsize=32)
def qualified_collections(paths: frozenset[str]) -> frozenset[CollectionShape]:
    """Collections behind a static segment that key their items unlike the plain collection of the same name.

    `/sites/{id}/managed/devices/{deviceId}` differs from `/devices/{serial}`; `/stock/devices/{serial}` does not.
    """
    placeholders = _collection_placeholders(paths)
    # `/urls/{list_type}` without `/urls` selects a list rather than naming an item
    listed = {_collection_shape(path) for path in paths}
    return _keyed_unlike_plain(placeholders, listed)


def item_collection(path: str) -> CollectionShape:
    """The collection whose items `path` addresses: `/managed/devices/{deviceId}` -> `("managed", "devices")`."""
    shape = _collection_shape(path)
    end = len(shape)
    while end and shape[end - 1] == "{}":
        end -= 1
    return shape[:end]


def _collection_placeholders(paths: Collection[str]) -> dict[CollectionShape, set[str]]:
    placeholders: dict[CollectionShape, set[str]] = {}
    for path in paths:
        shape = _collection_shape(path)
        end = 0
        for index, segment in enumerate(_collection_segments(path)):
            if shape[index] != "{}":
                end = index + 1
            elif end:
                # Every key of `/apps/{owner}/{app}` counts
                placeholders.setdefault(shape[:end], set()).add(segment)
    return placeholders


def _keyed_unlike_plain(
    placeholders: dict[CollectionShape, set[str]], listed: set[CollectionShape]
) -> frozenset[CollectionShape]:
    plain: dict[str, set[str]] = {}
    for shape, keys in placeholders.items():
        if len(shape) == 1 or shape[-2] == "{}":
            plain.setdefault(shape[-1], set()).update(keys)
    return frozenset(shape for shape, keys in placeholders.items() if _keyed_unlike(shape, keys, plain, listed))


def _keyed_unlike(
    shape: CollectionShape, keys: set[str], plain: dict[str, set[str]], listed: set[CollectionShape]
) -> bool:
    return (
        len(shape) > 1
        and shape[-2] != "{}"
        and shape in listed
        and shape[-1] in plain
        and _item_keys(plain[shape[-1]], shape[-1]).isdisjoint(_item_keys(keys, shape[-1]))
    )


def _item_keys(placeholders: set[str], collection: str) -> set[str]:
    # `{deviceId}`, `{device_id}`, `{device}` and `{id}` under `/devices` are one key
    prefix = normalize_for_matching(to_singular(collection))
    return {
        normalize_for_matching(placeholder.strip("{}")).removeprefix(prefix) or "id" for placeholder in placeholders
    }


@lru_cache(maxsize=2048)
def _collection_segments(path: str) -> tuple[str, ...]:
    return tuple(_resource_segments(strip_version_prefix(path)))


@lru_cache(maxsize=2048)
def _collection_shape(path: str) -> CollectionShape:
    return _shape(_collection_segments(path))


def _shape(segments: tuple[str, ...]) -> CollectionShape:
    return tuple("{}" if "{" in segment else segment for segment in segments)


IRREGULAR_TO_PLURAL = {
    "abuse": "abuses",
    "alias": "aliases",
    "analysis": "analyses",
    "anathema": "anathemata",
    "axe": "axes",
    "base": "bases",
    "bookshelf": "bookshelves",
    "cache": "caches",
    "canvas": "canvases",
    "carve": "carves",
    "case": "cases",
    "cause": "causes",
    "child": "children",
    "course": "courses",
    "criterion": "criteria",
    "database": "databases",
    "defense": "defenses",
    "diagnosis": "diagnoses",
    "die": "dice",
    "dingo": "dingoes",
    "disease": "diseases",
    "dogma": "dogmata",
    "dose": "doses",
    "eave": "eaves",
    "echo": "echoes",
    "enterprise": "enterprises",
    "ephemeris": "ephemerides",
    "excuse": "excuses",
    "expense": "expenses",
    "foot": "feet",
    "franchise": "franchises",
    "genus": "genera",
    "goose": "geese",
    "groove": "grooves",
    "half": "halves",
    "horse": "horses",
    "house": "houses",
    "human": "humans",
    "hypothesis": "hypotheses",
    "index": "indices",
    "knife": "knives",
    "lemma": "lemmata",
    "license": "licenses",
    "life": "lives",
    "loaf": "loaves",
    "looey": "looies",
    "man": "men",
    "matrix": "matrices",
    "mouse": "mice",
    "movie": "movies",
    "nose": "noses",
    "oasis": "oases",
    "ox": "oxen",
    "passerby": "passersby",
    "pause": "pauses",
    "person": "people",
    "phase": "phases",
    "phenomenon": "phenomena",
    "pickaxe": "pickaxes",
    "proof": "proofs",
    "purchase": "purchases",
    "purpose": "purposes",
    "quiz": "quizzes",
    "radius": "radii",
    "release": "releases",
    "response": "responses",
    "reuse": "reuses",
    "rose": "roses",
    "scarf": "scarves",
    "self": "selves",
    "sense": "senses",
    "shelf": "shelves",
    "size": "sizes",
    "snooze": "snoozes",
    "stigma": "stigmata",
    "stoma": "stomata",
    "synopsis": "synopses",
    "tense": "tenses",
    "thief": "thieves",
    "tooth": "teeth",
    "tornado": "tornadoes",
    "torpedo": "torpedoes",
    "use": "uses",
    "valve": "valves",
    "vase": "vases",
    "verse": "verses",
    "viscus": "viscera",
    "volcano": "volcanoes",
    "warehouse": "warehouses",
    "wave": "waves",
    "wife": "wives",
    "wolf": "wolves",
    "woman": "women",
    "yes": "yeses",
    "vie": "vies",
}
IRREGULAR_TO_SINGULAR = {v: k for k, v in IRREGULAR_TO_PLURAL.items()}
UNCOUNTABLE = frozenset(
    [
        "access",
        "address",
        "adulthood",
        "advice",
        "agenda",
        "aid",
        "aircraft",
        "alcohol",
        "alias",
        "ammo",
        "analysis",
        "analytics",
        "anime",
        "anonymous",
        "athletics",
        "audio",
        "bias",
        "bison",
        "blood",
        "bream",
        "buffalo",
        "butter",
        "carp",
        "cash",
        "chaos",
        "chassis",
        "chess",
        "clothing",
        "cod",
        "commerce",
        "compass",
        "consensus",
        "cooperation",
        "corps",
        "data",
        "debris",
        "deer",
        "diabetes",
        "diagnosis",
        "digestion",
        "elk",
        "energy",
        "ephemeris",
        "equipment",
        "eries",
        "excretion",
        "expertise",
        "firmware",
        "fish",
        "flounder",
        "fun",
        "gallows",
        "garbage",
        "graffiti",
        "hardware",
        "headquarters",
        "health",
        "herpes",
        "highjinks",
        "homework",
        "housework",
        "information",
        "jeans",
        "justice",
        "kudos",
        "labour",
        "literature",
        "machinery",
        "mackerel",
        "mail",
        "manga",
        "means",
        "media",
        "metadata",
        "mews",
        "money",
        "moose",
        "mud",
        "music",
        "news",
        "only",
        "personnel",
        "pike",
        "plankton",
        "pliers",
        "police",
        "pollution",
        "premises",
        "progress",
        "prometheus",
        "radius",
        "rain",
        "research",
        "rice",
        "salmon",
        "scissors",
        "series",
        "sewage",
        "shambles",
        "sheep",
        "shrimp",
        "software",
        "species",
        "staff",
        "swine",
        "synopsis",
        "tennis",
        "traffic",
        "transportation",
        "trout",
        "tuna",
        "wealth",
        "welfare",
        "whiting",
        "wildebeest",
        "wildlife",
        "wireless",
        "you",
    ]
)


@lru_cache(maxsize=2048)
def _is_word_like(s: str) -> bool:
    """Two or more characters, each a letter, `_` or `-`."""
    if not s or len(s) < 2:
        return False
    if not all(c.isalpha() or c in ("_", "-") for c in s):
        return False
    return not any(c.isdigit() for c in s)


@lru_cache(maxsize=2048)
def to_singular(word: str) -> str:
    if not _is_word_like(word):
        return word
    if word.lower() in UNCOUNTABLE:
        return word
    known_lower = IRREGULAR_TO_SINGULAR.get(word.lower())
    if known_lower is not None:
        if word[0].isupper():
            return known_lower.capitalize()
        return known_lower
    if word.endswith(("ss", "us")):
        return word
    if word.endswith("ies") and len(word) > 3 and word[-4] not in "aeiou":
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith(("xes", "zes", "ches", "shes")):
        return word[:-2]
    # `gases` -> `gas`; `-se` nouns like `case` come from the irregular table
    if word.endswith("ses") and len(word) > 3:
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


@lru_cache(maxsize=2048)
def to_plural(word: str) -> str:
    if not _is_word_like(word):
        return word
    if word.lower() in UNCOUNTABLE:
        return word
    known = IRREGULAR_TO_PLURAL.get(word)
    if known is not None:
        return known
    known_lower = IRREGULAR_TO_PLURAL.get(word.lower())
    if known_lower is not None:
        if word[0].isupper():
            return known_lower.capitalize()
        return known_lower
    # party -> parties, day -> days
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith("ss"):
        return word + "es"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


# Common identifier field names in priority order
ID_FIELD_NAMES = ["id", "uuid", "guid", "uid"]

# Request fields that page through a list (page-number, limit-offset and cursor pagination); never references.
PAGINATION_FIELDS = frozenset(
    {
        "page",
        "pagesize",
        "perpage",
        "size",
        "limit",
        "offset",
        "cursor",
        "start",
        "skip",
        "top",
        "after",
        "before",
        "since",
        "count",
        "first",
        "last",
        "pagenumber",
        "pageindex",
        "pageno",
        "pagenum",
    }
)


def find_matching_field(
    *, parameter: str, resource: str, fields: list[str], types: Mapping[str, set[str]] | None = None
) -> str | None:
    if not fields:
        return None
    if parameter in fields:
        return parameter
    resource_normalized = normalize_for_matching(resource)
    prefix, suffix = _split_parameter_name(parameter)
    for strategy in _FIELD_MATCHING_STRATEGIES:
        field = strategy(fields, parameter, prefix, suffix, resource_normalized)
        # Only an integer `pk` is a primary key; a string one partitions a table next to a sort key.
        if (
            field == "pk"
            and types is not None
            and types.get("pk") != {"integer"}
            and not normalize_for_matching(parameter).endswith("pk")
        ):
            continue
        if field is not None:
            return field
    return None


def parameter_names_resource_field(*, parameter: str, field: str, resource: str) -> bool:
    """Whether the part of `parameter` before `field` names `resource` or its trailing words."""
    parameter_normalized = normalize_for_matching(parameter)
    field_normalized = normalize_for_matching(field)
    if not parameter_normalized.endswith(field_normalized):
        return True
    hint = parameter_normalized[: -len(field_normalized)]
    if not hint:
        return True
    words = [word.lower() for word in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", resource)]
    return any("".join(words[start:]) == hint for start in range(len(words)))


def parameter_names_source(*, parameter: str, field: str, resource: str, pointer: str) -> bool:
    """Whether the part of `parameter` before `field` names `resource` or the response field holding the value."""
    hint = normalize_for_matching(parameter).removesuffix(normalize_for_matching(field))
    resource_normalized = normalize_for_matching(resource)
    # `check_name` takes `CheckRun.name`, `tunnel_name` takes `VpnTunnel.name`.
    if not hint or resource_normalized.startswith(hint) or resource_normalized.endswith(hint):
        return True
    # `order__orderer__name` takes `/orderer/name`, not `/contact/name`, though both hold a `Contact`.
    segments = [segment for segment in pointer.split("/") if segment and segment != "*"]
    return bool(segments) and hint.endswith(normalize_for_matching(segments[-1]))


def _field_normalized_as(fields: list[str], expected: str) -> str | None:
    for field in fields:
        if normalize_for_matching(field) == expected:
            return field
    return None


def _match_normalized(
    fields: list[str], parameter: str, prefix: str, suffix: str, resource_normalized: str
) -> str | None:
    # `brandId` -> `Brand.BrandId`
    return _field_normalized_as(fields, normalize_for_matching(parameter))


def _match_resource_prefixed_parameter(
    fields: list[str], parameter: str, prefix: str, suffix: str, resource_normalized: str
) -> str | None:
    # `channelId` -> `Channel.id`
    if prefix and normalize_for_matching(prefix) == resource_normalized:
        return _field_normalized_as(fields, normalize_for_matching(suffix))
    return None


def _match_resource_prefixed_field(
    fields: list[str], parameter: str, prefix: str, suffix: str, resource_normalized: str
) -> str | None:
    # `id` -> `Channel.channelId`
    if not prefix and suffix:
        return _field_normalized_as(fields, resource_normalized + normalize_for_matching(suffix))
    return None


def _match_identifier_synonym(
    fields: list[str], parameter: str, prefix: str, suffix: str, resource_normalized: str
) -> str | None:
    # `conversation_id` or `id` -> `uuid`, `guid`, `uid`
    suffix_normalized = normalize_for_matching(suffix)
    # Composite suffixes like `_id_or_slug` try identifier fields first, then slug.
    if suffix_normalized == "idorslug":
        names = [*ID_FIELD_NAMES, "slug"]
    elif suffix_normalized in ("identifier", "pk"):
        names = ["id"]
    elif suffix_normalized in ("id", "ids"):
        names = [*ID_FIELD_NAMES, "pk"]
    elif suffix_normalized in ID_FIELD_NAMES:
        names = ID_FIELD_NAMES
    else:
        return None
    for name in names:
        field = _field_normalized_as(fields, name)
        if field is not None:
            return field
    return None


def _match_resource_hint(
    fields: list[str], parameter: str, prefix: str, suffix: str, resource_normalized: str
) -> str | None:
    # `file_name` -> `BackupFile.name`: the part before the last underscore hints at the resource
    last_underscore = parameter.rfind("_")
    if last_underscore <= 0:
        return None
    hint = parameter[:last_underscore]
    field_name = parameter[last_underscore + 1 :]
    # A hint shorter than 3 characters produces spurious matches.
    if len(hint) < 3 or not field_name:
        return None
    hint_normalized = normalize_for_matching(hint)
    if resource_normalized.endswith(hint_normalized) or resource_normalized.startswith(hint_normalized):
        return _field_normalized_as(fields, normalize_for_matching(field_name))
    return None


# Priority order: the first strategy that finds a field wins.
_FIELD_MATCHING_STRATEGIES = (
    _match_normalized,
    _match_resource_prefixed_parameter,
    _match_resource_prefixed_field,
    _match_identifier_synonym,
    _match_resource_hint,
)


@lru_cache(maxsize=4096)
def normalize_for_matching(text: str) -> str:
    """Lowercase without `_` and `-`: `channel_id` -> `channelid`."""
    return text.lower().replace("_", "").replace("-", "")


@lru_cache(maxsize=2048)
def _split_parameter_name(parameter_name: str) -> tuple[str, str]:
    """Split into (prefix, suffix): `channelId` -> (`channel`, `Id`); no known suffix gives ("", parameter_name)."""
    # camelCase suffixes, longest first
    for cap_suffix in ("Uuid", "Guid", "Name", "Slug", "Id"):
        if parameter_name.endswith(cap_suffix) and len(parameter_name) > len(cap_suffix):
            return parameter_name[: -len(cap_suffix)], cap_suffix

    # Composite suffixes before shorter ones
    if parameter_name.endswith("_id_or_slug") and len(parameter_name) > 11:
        return parameter_name[:-11], "_id_or_slug"

    if parameter_name.endswith("-id-or-slug") and len(parameter_name) > 11:
        return parameter_name[:-11], "-id-or-slug"

    if parameter_name.endswith("_id") and len(parameter_name) > 3:
        return parameter_name[:-3], "_id"

    if parameter_name.endswith("_pk") and len(parameter_name) > 3:
        return parameter_name[:-3], "_pk"

    if parameter_name.endswith("_guid") and len(parameter_name) > 5:
        return parameter_name[:-5], "_guid"

    if parameter_name.endswith("_slug") and len(parameter_name) > 5:
        return parameter_name[:-5], "_slug"

    if parameter_name.endswith("pk") and len(parameter_name) > 2:
        return parameter_name[:-2], "pk"

    return "", parameter_name


def strip_affixes(name: str, prefixes: list[str], suffixes: list[str]) -> str:
    """Remove one of `prefixes` and one of `suffixes`, matched against the lowercased name."""
    result = name.strip()
    name_lower = result.lower()

    for prefix in prefixes:
        if name_lower.startswith(prefix):
            result = result[len(prefix) :]
            break

    for suffix in suffixes:
        if name_lower.endswith(suffix):
            result = result[: -len(suffix)]
            break

    return result.strip()


_HYPHENATED_SCHEMA_SUFFIXES = ("-Output", "-Input", "-Response", "-Request")
# No `Response`/`Request` (often wrappers) or `Summary` (may use another identifier)
_PASCALCASE_SCHEMA_SUFFIXES = ("Output", "Input", "Out", "In", "DTO", "Dto")
_MIN_BASE_LENGTH = 2


@lru_cache(maxsize=2048)
def normalize_schema_name(name: str) -> str:
    """Normalize schema name by removing common suffixes (-Output, Out, DTO, etc.)."""
    if not name or len(name) < _MIN_BASE_LENGTH + 2:
        return name

    for suffix in _HYPHENATED_SCHEMA_SUFFIXES:
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            if len(base) >= _MIN_BASE_LENGTH:
                return base
            return name

    for suffix in _PASCALCASE_SCHEMA_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[: -len(suffix)]
            if len(base) >= _MIN_BASE_LENGTH and base[-1].islower():
                return base

    return name


def collect_candidate_resource_names(raw_schema: dict) -> frozenset[str]:
    """Resource names backed by a path segment or component schema in this spec.

    Body-field FK suffixes create a resource only with this backing, as `first_name` is more likely an attribute.
    """
    names: set[str] = set()

    paths = raw_schema.get("paths") or {}
    if isinstance(paths, dict):
        for path in paths:
            if not isinstance(path, str):
                continue
            stripped = strip_version_prefix(path)
            for segment in stripped.split("/"):
                if not segment or "{" in segment:
                    continue
                singular = to_singular(segment)
                names.add(to_pascal_case(singular))

    schema_containers: list[dict] = []
    components = raw_schema.get("components")
    if isinstance(components, dict):
        component_schemas = components.get("schemas")
        if isinstance(component_schemas, dict):
            schema_containers.append(component_schemas)
    definitions = raw_schema.get("definitions")
    if isinstance(definitions, dict):
        schema_containers.append(definitions)
    for container in schema_containers:
        for name in container:
            if not isinstance(name, str) or not name:
                continue
            names.add(name)
            normalized = normalize_schema_name(name)
            if normalized:
                names.add(normalized)

    return frozenset(names)
