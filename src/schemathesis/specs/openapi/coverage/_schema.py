"""JSON Schema constraint walking.

Produces positive and negative coverage values for individual schema constructs
(`type`, `enum`, `pattern`, `minimum`, `oneOf`, ...).
"""

from __future__ import annotations

import re
import string
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache, partial
from hashlib import blake2b
from itertools import combinations, count, islice
from math import ceil, floor, inf, nextafter, ulp

from schemathesis.core.jsonschema import (
    CANONICALIZE_DRAFT_BY_VALIDATOR,
    FANCY_REGEX_OPTIONS,
    VALIDATED_FORMATS_BY_DRAFT,
    compile_ecma_pattern,
    is_valid,
    make_validator,
    make_validator_for,
    make_validator_with_seed,
)
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.keywords import ALL_KEYWORDS
from schemathesis.core.jsonschema.numeric import (
    bounds_are_unsatisfiable,
    is_numeric_bound,
    next_float32,
    resolve_inclusive_bounds,
)

try:
    from json.encoder import _make_iterencode  # type: ignore[attr-defined]
except ImportError:
    _make_iterencode = None

try:
    from json.encoder import c_make_encoder  # type: ignore[attr-defined]
except ImportError:
    c_make_encoder = None

from collections.abc import Callable, Generator, Iterator
from json.encoder import JSONEncoder, encode_basestring_ascii
from typing import Any, TypeVar, cast
from urllib.parse import quote

import jsonschema_rs
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument, Unsatisfiable

from schemathesis.core import (
    INTERNAL_BUFFER_SIZE,
    MAX_GENERATED_ITEMS,
    MAX_GENERATED_PATTERN_LENGTH,
    MAX_STRING_LENGTH,
    NOT_SET,
)
from schemathesis.core.cache import MISSING, BoundedCache
from schemathesis.core.errors import InvalidSchema, RefResolutionError
from schemathesis.core.jsonschema.resolver import Resolver, make_root_resolver, resolve_reference
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, get_type, to_json_type_name
from schemathesis.core.media_types import is_form_parts, is_xml_parts
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable
from schemathesis.generation import GenerationMode
from schemathesis.generation._cache import schema_cache_key
from schemathesis.generation.hypothesis import UNSATISFIABLE_RESULT, examples, schema_generation_cache
from schemathesis.generation.jsonschema import build
from schemathesis.generation.meta import CoverageScenario
from schemathesis.openapi.generation.filters import is_invalid_path_parameter
from schemathesis.specs.openapi.converter import apply_rewritten_pattern
from schemathesis.specs.openapi.patterns import (
    matches_every_string,
    pattern_length_bounds,
    pattern_length_is_unreachable,
    pattern_requires_char_outside,
    pattern_requires_literal,
    pin_pattern_length,
)
from schemathesis.transport.serialization import contains_binary

VALIDATED_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "duration",
        "email",
        "hostname",
        "idn-email",
        "idn-hostname",
        "ipv4",
        "ipv6",
        "iri",
        "iri-reference",
        "json-pointer",
        "regex",
        "relative-json-pointer",
        "time",
        "uri",
        "uri-reference",
        "uri-template",
        "uuid",
    }
)

_FORMAT_VALIDATORS: dict[tuple[str, type], jsonschema_rs.Validator] = {}


def _get_format_validator(format: str, validator_cls: type[jsonschema_rs.Validator]) -> jsonschema_rs.Validator:
    """Get or create a cached validator for checking a specific format."""
    key = (format, validator_cls)
    if key not in _FORMAT_VALIDATORS:
        _FORMAT_VALIDATORS[key] = make_validator({"type": "string", "format": format}, validator_cls)
    return _FORMAT_VALIDATORS[key]


def conforms_to_format(value: object, format: str, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    """Check if a value conforms to a JSON Schema format."""
    return _get_format_validator(format, validator_cls).is_valid(value)


def _remove_examples(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove 'examples' field from a schema for jsonschema-rs compatibility."""
    # Sub-schemas reached via `$ref` are the same dict instance across calls, so id-keyed
    # caching saves rewalking shared definitions (e.g. k8s ObjectMeta referenced everywhere).
    cached = _REMOVE_EXAMPLES_CACHE.get(id(schema))
    if cached is not MISSING:
        return cached[0]
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "examples":
            continue
        if isinstance(value, dict):
            result[key] = _remove_examples(value)
        elif isinstance(value, list):
            result[key] = [_remove_examples(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    # The second element pins `schema`, so its `id` cannot be recycled into a stale hit.
    _REMOVE_EXAMPLES_CACHE[id(schema)] = (result, schema)
    return result


_REMOVE_EXAMPLES_CACHE: BoundedCache = BoundedCache(maxsize=4096)


def _replace_zero_with_nonzero(x: float) -> float:
    return x or 0.0


def _judges(schema: JsonSchema, ctx: CoverageContext) -> list[jsonschema_rs.Validator]:
    """The operation's draft, plus the latest draft for the assertions it adds (`const`, formats); each that loads."""
    full_schema: JsonSchema = schema
    if isinstance(schema, dict) and BUNDLE_STORAGE_KEY in ctx.root_schema and BUNDLE_STORAGE_KEY not in schema:
        full_schema = {**schema, BUNDLE_STORAGE_KEY: ctx.root_schema[BUNDLE_STORAGE_KEY]}
    latest = jsonschema_rs.validator_cls_for(full_schema)
    judges: list[jsonschema_rs.Validator] = []
    for validator_cls in dict.fromkeys((ctx.validator_cls, latest)):
        try:
            judges.append(make_validator(_spelled_for(full_schema, validator_cls, ctx), validator_cls))
        except Exception:
            continue
    return judges


def _spelled_for(schema: JsonSchema, validator_cls: type, ctx: CoverageContext) -> JsonSchema:
    """The schema as values are drawn for this draft: Draft 4 reads `const` only once it is spelled as `enum`."""
    if validator_cls is not jsonschema_rs.Draft4Validator or not isinstance(schema, dict):
        return schema
    bundle = schema.get(BUNDLE_STORAGE_KEY)
    if bundle is None:
        return _prepared(schema, draft4=True)
    rest = {key: value for key, value in schema.items() if key != BUNDLE_STORAGE_KEY}
    return {**_prepared(rest, draft4=True), BUNDLE_STORAGE_KEY: _ready_bundle(bundle, None, True)}


def _admitted(value: Any, schema: JsonSchema, ctx: CoverageContext, *, unjudged: bool) -> bool:
    """Whether every judge that loads admits the value; `unjudged` answers when none does."""
    judges = _judges(schema, ctx)
    if not judges:
        return unjudged
    return all(judge.is_valid(value) for judge in judges)


def _is_strictly_valid(value: Any, schema: dict[str, Any], ctx: CoverageContext) -> bool:
    # Fails closed, so a value nothing can check is dropped rather than shipped as a valid positive.
    return _admitted(value, schema, ctx, unjudged=False)


def _without_forbidden_keys(value: Any, schema: dict[str, Any]) -> Any:
    # Spec hints reflecting the response shape may carry `readOnly` keys that request-side
    # schemas forbid as `{"not": {}}`; dropping those keys recovers the curated value.
    if not isinstance(value, dict):
        return NOT_SET
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return NOT_SET
    forbidden = {k for k, sub in properties.items() if isinstance(sub, dict) and sub.get("not") == {}}
    if not forbidden or forbidden.isdisjoint(value):
        return NOT_SET
    return {k: v for k, v in value.items() if k not in forbidden}


def _accept_spec_value(value: Any, schema: dict[str, Any], ctx: CoverageContext) -> Any:
    if _is_strictly_valid(value, schema, ctx):
        return value
    cleaned = _without_forbidden_keys(value, schema)
    if cleaned is not NOT_SET and _is_strictly_valid(cleaned, schema, ctx):
        return cleaned
    return NOT_SET


def json_recursive_strategy(strategy: st.SearchStrategy) -> st.SearchStrategy:
    return st.lists(strategy, max_size=2) | st.dictionaries(st.text(), strategy, max_size=2)


# Keywords that describe a schema without restricting the values it accepts.
ANNOTATION_KEYWORDS = frozenset(("description", "example", "examples", "title", "deprecated", "externalDocs", "xml"))
NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN = 100
NEGATIVE_MODE_MAX_ITEMS = 15
# Longest array still drawn element by element; a pattern-matched element spends budget per
# character, so past this the draw stops being affordable.
MAX_DRAWN_ARRAY_ITEMS = 64
# Largest object still drawn whole; free values past this overrun the buffer before the floor is met.
MAX_DRAWN_OBJECT_PROPERTIES = 64
FLOAT_STRATEGY: st.SearchStrategy = st.floats(allow_nan=False, allow_infinity=False).map(_replace_zero_with_nonzero)
NUMERIC_STRATEGY: st.SearchStrategy = st.integers() | FLOAT_STRATEGY
JSON_STRATEGY: st.SearchStrategy = st.recursive(
    st.none() | st.booleans() | NUMERIC_STRATEGY | st.text(max_size=16),
    json_recursive_strategy,
    max_leaves=2,
)
ARRAY_STRATEGY: st.SearchStrategy = st.lists(JSON_STRATEGY, min_size=2, max_size=3)
OBJECT_STRATEGY: st.SearchStrategy = st.dictionaries(st.text(max_size=16), JSON_STRATEGY, max_size=2)
# Alphabetic non-empty string used for wrong-type negatives; shrinks to "AAA".
# Plain `st.text()` shrinks to "", which serializes to absent on the wire
# (`?p=`, empty header, empty body) and defeats the type violation.
NEGATIVE_STRING_STRATEGY: st.SearchStrategy = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=122, categories=["L"]),
    min_size=3,
)
# What a draw settles on once it shrinks. Where only the text reaches the server, these stand in
# for a search. Alternatives are tried in order until one breaks the schema.
STRINGIFIED_TYPE_PROBES: dict[str, tuple[Any, ...]] = {
    # `2` stands in where a boolean is declared, since `0` reads as one on the wire.
    "integer": (0, 2),
    # Non-integer, so it stays distinct from the integer above.
    "number": (0.5,),
    "boolean": (True, False),
    "null": (None,),
    "string": ("AAA",),
    "array": ([None, None],),
    "object": ({},),
}


STRATEGIES_FOR_TYPE = {
    "integer": st.integers(),
    "number": NUMERIC_STRATEGY,
    "boolean": st.booleans(),
    "null": st.none(),
    "string": st.text(),
    "array": ARRAY_STRATEGY,
    "object": OBJECT_STRATEGY,
}


def get_strategy_for_type(ty: str | list[str]) -> st.SearchStrategy:
    if isinstance(ty, str):
        return STRATEGIES_FOR_TYPE[ty]
    return st.one_of(STRATEGIES_FOR_TYPE[t] for t in ty if t in STRATEGIES_FOR_TYPE)


UNKNOWN_PROPERTY_KEY = "x-schemathesis-unknown-property"
UNKNOWN_PROPERTY_VALUE = 42
ADDITIONAL_PROPERTY_KEY_BASE = "x-schemathesis-additional"


def _generate_additional_property_key(existing_keys: set[str], start: int = 0) -> str:
    """The first free numbered key at or past `start`; a caller taking keys in order passes how many it has taken."""
    counter = start
    key = ADDITIONAL_PROPERTY_KEY_BASE if counter == 0 else f"{ADDITIONAL_PROPERTY_KEY_BASE}{counter}"
    while key in existing_keys:
        counter += 1
        key = f"{ADDITIONAL_PROPERTY_KEY_BASE}{counter}"
    return key


_UNEXPECTED_PROPERTY_KEYS = (UNKNOWN_PROPERTY_KEY, "schemathesis-unknown-property", "unknown-property-0")


def _pattern_property_regexes(schema: dict) -> list[re.Pattern[str]]:
    regexes: list[re.Pattern[str]] = []
    for pattern in schema.get("patternProperties", {}):
        try:
            regexes.append(re.compile(pattern))
        except re.error:
            continue
    return regexes


def _unexpected_property_key(schema: dict, existing_keys: set[str]) -> str | None:
    # An additional property must match neither a declared name nor any `patternProperties`
    # pattern, otherwise it stays valid under `additionalProperties: false`.
    patterns = _pattern_property_regexes(schema)
    for candidate in _UNEXPECTED_PROPERTY_KEYS:
        if candidate not in existing_keys and not any(pattern.search(candidate) for pattern in patterns):
            return candidate
    return None


@dataclass
class GeneratedValue:
    value: Any
    generation_mode: GenerationMode
    scenario: CoverageScenario
    description: str
    parameter: str | None
    location: str | None

    __slots__ = ("value", "generation_mode", "scenario", "description", "parameter", "location")

    @classmethod
    def with_positive(cls, value: Any, *, scenario: CoverageScenario, description: str) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.POSITIVE,
            scenario=scenario,
            description=description,
            location=None,
            parameter=None,
        )

    @classmethod
    def with_negative(
        cls, value: Any, *, scenario: CoverageScenario, description: str, location: str, parameter: str | None = None
    ) -> GeneratedValue:
        return cls(
            value=value,
            generation_mode=GenerationMode.NEGATIVE,
            scenario=scenario,
            description=description,
            location=location,
            parameter=parameter,
        )


PositiveValue = GeneratedValue.with_positive
NegativeValue = GeneratedValue.with_negative


@lru_cache(maxsize=128)
def _draw_outcome(strategy: st.SearchStrategy) -> tuple[Any, Unsatisfiable | None]:
    # Draws are seeded, so a strategy that yields nothing yields nothing every time - and finding
    # that out costs the whole generation budget, which is far too much to pay twice.
    try:
        return examples.generate_one(strategy), None
    except Unsatisfiable as exc:
        return None, exc


def cached_draw(strategy: st.SearchStrategy) -> Any:
    value, failure = _draw_outcome(strategy)
    if failure is not None:
        raise failure.with_traceback(None)
    return value


def _keeps_length_within(pattern: str, min_length: int | None, max_length: int | None) -> bool:
    """Whether every string the pattern matches already lands inside the length window."""
    pattern_min, pattern_max = pattern_length_bounds(pattern)
    if min_length is not None and pattern_min < min_length:
        return False
    return max_length is None or (pattern_max is not None and pattern_max <= max_length)


@lru_cache(maxsize=128)
def _pattern_strategy(
    pattern: str, min_length: int | None, max_length: int | None, fmt: str | None
) -> st.SearchStrategy | None:
    """Strings the pattern matches within the length bounds, or `None` when Python `re` cannot read it."""
    # Memoized because `cached_draw` keys on the strategy itself: a pattern rebuilt per draw arrives as
    # a fresh object every time, so the same regex gets drawn from again instead of answering from cache.
    compiled = compile_ecma_pattern(pattern)
    if compiled is None:
        return None
    strategy = st.from_regex(compiled, fullmatch=True)
    if min_length is not None and max_length is not None:
        strategy = strategy.filter(lambda s: min_length <= len(s) <= max_length)
    elif min_length is not None:
        strategy = strategy.filter(lambda s: len(s) >= min_length)
    elif max_length is not None:
        strategy = strategy.filter(lambda s: len(s) <= max_length)
    if fmt is not None:
        strategy = strategy.filter(make_validator_for({"type": "string", "format": fmt}).is_valid)
    return strategy


@dataclass
class CoverageContext:
    root_schema: dict[str, Any]
    generation_modes: list[GenerationMode]
    location: ParameterLocation
    media_type: tuple[str, str] | None
    is_required: bool
    path: list[str | int]
    custom_formats: dict[str, st.SearchStrategy]
    validator_cls: type[jsonschema_rs.Validator]
    update_pattern: Callable[[str, int | None, int | None], str] | None
    _resolver: Resolver | None
    allow_extra_parameters: bool
    expanding: dict[str, int]
    generating: dict[str, int]

    __slots__ = (
        "root_schema",
        "location",
        "media_type",
        "generation_modes",
        "is_required",
        "path",
        "_path_str_cache_cell",
        "custom_formats",
        "validator_cls",
        "update_pattern",
        "_resolver",
        "_root_token_cell",
        "allow_extra_parameters",
        "expanding",
        "generating",
    )

    def __init__(
        self,
        *,
        root_schema: dict[str, Any],
        location: ParameterLocation,
        media_type: tuple[str, str] | None,
        generation_modes: list[GenerationMode] | None = None,
        is_required: bool,
        path: list[str | int] | None = None,
        custom_formats: dict[str, st.SearchStrategy],
        validator_cls: type[jsonschema_rs.Validator],
        update_pattern: Callable[[str, int | None, int | None], str] | None = None,
        _resolver: Resolver | None = None,
        _path_str_cache_cell: list[str | None] | None = None,
        _root_token_cell: list[object] | None = None,
        allow_extra_parameters: bool = True,
        expanding: dict[str, int] | None = None,
        generating: dict[str, int] | None = None,
    ) -> None:
        self.root_schema = root_schema
        self.location = location
        self.media_type = media_type
        self.generation_modes = generation_modes if generation_modes is not None else list(GenerationMode)
        self.is_required = is_required
        self.path = path or []
        # Single-cell cache for the joined path string. with_positive / with_negative share the
        # cell so any context that mutates the shared path list (via at()) invalidates the cache
        # for all contexts pointing at it.
        self._path_str_cache_cell: list[str | None] = (
            _path_str_cache_cell if _path_str_cache_cell is not None else [None]
        )
        self.custom_formats = custom_formats
        self.validator_cls = validator_cls
        self.update_pattern = update_pattern
        self._resolver = _resolver
        # Shared like the path cell: every context over this document answers with the same token.
        self._root_token_cell: list[object] = _root_token_cell if _root_token_cell is not None else [None]
        self.allow_extra_parameters = allow_extra_parameters
        # How deep the walk is inside each reference, shared with every context derived from this one.
        self.expanding = expanding if expanding is not None else {}
        # The same, for building one value; a value nests on its own budget, not the walk's.
        self.generating = generating if generating is not None else {}

    def __repr__(self) -> str:
        # Bound methods are used as Hypothesis filter predicates; the default slot dump
        # would push the full `root_schema` into every retry event in `ConjectureData.events`.
        return f"<CoverageContext path={self.current_path!r}>"

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def resolver(self) -> Resolver:
        """Lazy-initialized cached resolver."""
        if self._resolver is None:
            self._resolver = make_root_resolver(self.root_schema)
        return self._resolver

    def resolve_ref(self, ref: str) -> dict | bool:
        """Resolve a $ref to its schema definition."""
        _, resolved = resolve_reference(self.resolver, ref)
        return resolved

    def schema_key(self, schema: JsonSchema) -> tuple[object, ...]:
        """A cache key for what generation reads: the schema, and the document behind any reference in it."""
        return (schema_cache_key(schema), self._root_token() if _reads_references(schema) else None)

    def _root_token(self) -> object:
        if self._root_token_cell[0] is None:
            self._root_token_cell[0] = _to_hashable_key(self.root_schema)
        return self._root_token_cell[0]

    @contextmanager
    def expand(self, reference: str, *, counters: dict[str, int] | None = None) -> Generator[None, None, None]:
        """Go into a reference, counting how many times this one is already open."""
        counters = self.expanding if counters is None else counters
        counters[reference] = counters.get(reference, 0) + 1
        try:
            yield
        finally:
            depth = counters[reference] - 1
            if depth:
                counters[reference] = depth
            else:
                del counters[reference]

    def is_exhausted(self, reference: str, *, counters: dict[str, int] | None = None) -> bool:
        """Whether this reference has been gone through as far as it goes.

        A cycle has no end, so the walk needs one. Going around it twice leaves the position that
        points back carrying the schema it names, which is what a negative case there has to break.
        That second pass is where this walk ends: pointers below it stay closed, because letting each
        one open again multiplies the walks through a graph of cycles instead of adding to them.
        """
        counters = self.expanding if counters is None else counters
        depth = counters.get(reference, 0)
        if depth >= 2:
            return True
        return any(other >= 2 for other in counters.values())

    @contextmanager
    def at(self, key: str | int) -> Generator[None, None, None]:
        self.path.append(key)
        self._path_str_cache_cell[0] = None
        try:
            yield
        finally:
            self.path.pop()
            self._path_str_cache_cell[0] = None

    @property
    def current_path(self) -> str:
        cached = self._path_str_cache_cell[0]
        if cached is None:
            cached = "/" + "/".join(str(key) for key in self.path)
            self._path_str_cache_cell[0] = cached
        return cached

    def with_positive(self) -> CoverageContext:
        return CoverageContext(
            root_schema=self.root_schema,
            location=self.location,
            media_type=self.media_type,
            generation_modes=[GenerationMode.POSITIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
            update_pattern=self.update_pattern,
            _resolver=self._resolver,
            _path_str_cache_cell=self._path_str_cache_cell,
            _root_token_cell=self._root_token_cell,
            allow_extra_parameters=self.allow_extra_parameters,
            expanding=self.expanding,
            generating=self.generating,
        )

    def with_negative(self) -> CoverageContext:
        return CoverageContext(
            root_schema=self.root_schema,
            location=self.location,
            media_type=self.media_type,
            generation_modes=[GenerationMode.NEGATIVE],
            is_required=self.is_required,
            path=self.path,
            custom_formats=self.custom_formats,
            validator_cls=self.validator_cls,
            update_pattern=self.update_pattern,
            _resolver=self._resolver,
            _path_str_cache_cell=self._path_str_cache_cell,
            _root_token_cell=self._root_token_cell,
            allow_extra_parameters=self.allow_extra_parameters,
            expanding=self.expanding,
            generating=self.generating,
        )

    def is_valid_for_location(self, value: Any) -> bool:
        if self.location in ("header", "cookie") and isinstance(value, str):
            return not value or (is_latin_1_encodable(value) and not has_invalid_characters("A", value))
        elif self.location == "path":
            return not is_invalid_path_parameter(value)
        return True

    def leads_to_negative_test_case(self, value: Any) -> bool:
        if self.location == "query":
            # Some values will not be serialized into the query string
            if isinstance(value, list) and not self.is_required:
                # Optional parameters should be present
                return any(item not in [{}, []] for item in value)
        return True

    def will_be_serialized_to_string(self) -> bool:
        if self.location in ("query", "path", "header", "cookie"):
            return True
        if self.location == "body" and self.media_type is not None:
            if is_form_parts(self.media_type):
                return True
            if is_xml_parts(self.media_type):
                return True
        return False

    def can_be_negated(self, schema: JsonSchemaObject) -> bool:
        # Path, query, header, and cookie parameters will be stringified anyway
        # If there are no constraints, then anything will match the original schema after serialization
        if self.will_be_serialized_to_string():
            cleaned = {
                k: v
                for k, v in schema.items()
                if not k.startswith("x-") and k not in ["description", "example", "examples"]
            }
            return cleaned not in [{}, {"type": "string"}]
        return True

    def generate_from(self, strategy: st.SearchStrategy) -> Any:
        return cached_draw(strategy)

    def build_strategy(self, schema: JsonSchema) -> st.SearchStrategy | None:
        draft = CANONICALIZE_DRAFT_BY_VALIDATOR[self.validator_cls]
        draft4 = draft == jsonschema_rs.Draft4
        if not isinstance(schema, dict) or (bundle := schema.get(BUNDLE_STORAGE_KEY)) is None:
            prepared = _prepared(schema, draft4=draft4)
        else:
            # The bundle carries every definition in the document and is the same for all values,
            # so it is reshaped once instead of rewalked for each one.
            rest = {key: value for key, value in schema.items() if key != BUNDLE_STORAGE_KEY}
            prepared = {
                **_prepared(rest, draft4=draft4),
                BUNDLE_STORAGE_KEY: _ready_bundle(bundle, self.update_pattern, draft4),
            }
        strategy = self._build(prepared, draft)
        if strategy is not None or not isinstance(prepared, dict):
            return strategy
        wider = _without_conditionals(prepared)
        if wider is None:
            return None
        strategy = self._build(wider, draft)
        judge = _judge(schema, self)
        if strategy is None or judge is None:
            return None
        # The wider form admits values the schema rules out, so the validator has the last word.
        return strategy.filter(judge)

    def _build(self, schema: JsonSchema, draft: int) -> st.SearchStrategy | None:
        # This phase reshapes what it is given and covers whatever parses, so a definition the draft
        # rejects - the caller's or one of these rewrites - leaves the branch uncovered, not the run failed.
        try:
            return build(schema, draft=draft, formats=self.custom_formats)
        except InvalidSchema:
            return None

    def _without_unbuildable_optional_properties(self, schema: JsonSchemaObject) -> JsonSchemaObject | None:
        """The same object without the optional properties nothing can be drawn for."""
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        required = set(schema.get("required", []))
        bundle = schema.get(BUNDLE_STORAGE_KEY)
        kept = {}
        for name, sub_schema in properties.items():
            candidate = sub_schema
            if bundle is not None and isinstance(candidate, dict):
                candidate = {**candidate, BUNDLE_STORAGE_KEY: bundle}
            if self.build_strategy(candidate) is not None:
                kept[name] = sub_schema
            elif name in required:
                return None
        if len(kept) == len(properties):
            return None
        return {**schema, "properties": kept}

    def _generate_around_conditionals(self, schema: JsonSchemaObject, described: JsonSchemaObject) -> Any:
        """A value for `schema` drawn from the part of it that describes values, or `NOT_SET`."""
        try:
            candidate = self._generate_from_schema_inner(described)
        except (InvalidArgument, Unsatisfiable):
            return NOT_SET
        judge = _judge(schema, self)
        if judge is None or not judge(candidate):
            return NOT_SET
        return candidate

    def _tiled_array(self, schema: JsonSchemaObject, length: int) -> list:
        """An array of this length, repeated from a one-element draw rather than drawn whole."""
        # A one-element array rather than a bare element: drawing the element on its own skips how
        # the schema's `pattern` gets reconciled with the configured alphabet.
        item = self.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1})[0]
        # A shared element would let one caller's edit show up at every other index.
        if isinstance(item, (dict, list)):
            return [deepclone(item) for _ in range(length)]
        return [item] * length

    def _filled_object(self, schema: JsonSchemaObject, size: int) -> dict[str, Any]:
        """An object of this size, one drawn value repeated under fresh names rather than drawn whole."""
        # A copy, so the names do not land on the draw every later caller of the same schema gets.
        result = dict(
            self.generate_from_schema({key: value for key, value in schema.items() if key != "minProperties"})
        )
        additional = schema.get("additionalProperties", True)
        filler = self.generate_from_schema(additional if isinstance(additional, dict) else {})
        declared = schema.get("properties", {})
        for index in count():
            if len(result) >= size:
                break
            name = f"x{index}"
            if name in result or name in declared:
                continue
            # A shared value would let one caller's edit show up under every other name.
            result[name] = deepclone(filler) if isinstance(filler, (dict, list)) else filler
        return result

    def _long_string_matching(self, schema: JsonSchemaObject, length: int) -> str:
        """A string this long the `pattern` accepts, for lengths matching it cannot draw."""
        # A pattern that does not restrict characters is satisfied by a plain string of that length,
        # which costs nothing to build; a stricter one has no value at this size at all.
        compiled = compile_ecma_pattern(schema["pattern"])
        if compiled is None:
            raise Unsatisfiable
        without_pattern = {key: value for key, value in schema.items() if key != "pattern"}
        candidate = self.generate_from_schema({**without_pattern, "minLength": length, "maxLength": length})
        if not isinstance(candidate, str) or not compiled.search(candidate):
            raise Unsatisfiable
        return candidate

    def generate_from_schema(self, schema: JsonSchema) -> Any:
        if isinstance(schema, dict) and "$ref" in schema:
            reference = schema["$ref"]
            if self.is_exhausted(reference, counters=self.generating):
                # The value would have to keep nesting, so there is nothing finite to put here.
                # An optional property drops out; a required one takes the whole value with it.
                raise Unsatisfiable
            with self.expand(reference, counters=self.generating):
                # Deep clone to avoid circular references in Python objects
                return self._generate_from_resolved(deepclone(self.resolve_ref(reference)))
        return self._generate_from_resolved(schema)

    def _generate_from_resolved(self, schema: JsonSchema) -> Any:
        if isinstance(schema, bool):
            if not schema:
                raise Unsatisfiable
            return 0
        # Same parameter shape recurs verbatim across operations (shared auth/header params), and
        # unsatisfiable schemas (e.g. JS-style `/.../`-wrapped regex) cost seconds per Hypothesis call.
        try:
            cache_key = (
                self.schema_key(schema),
                id(self.custom_formats),
                id(self.update_pattern),
                self.validator_cls,
            )
        except (TypeError, ValueError):
            cache_key = None
        if cache_key is not None:
            cached = schema_generation_cache.get(cache_key)
            if cached is UNSATISFIABLE_RESULT:
                raise Unsatisfiable
            if cached is not MISSING:
                return deepclone(cached) if isinstance(cached, (dict, list)) else cached
        try:
            value = self._generate_from_schema_inner(schema)
        except Unsatisfiable:
            if cache_key is not None:
                schema_generation_cache[cache_key] = UNSATISFIABLE_RESULT
            raise
        if isinstance(value, list) and isinstance(schema, dict) and "contains" in schema:
            value = _ensure_contains_bounds(self, value, schema)
        if cache_key is not None:
            schema_generation_cache[cache_key] = deepclone(value) if isinstance(value, (dict, list)) else value
        return value

    def _generate_from_schema_inner(self, schema: JsonSchemaObject) -> Any:
        if isinstance(schema, dict):
            described = _prepared(schema, drop=_CONDITIONAL_KEYS)
            if described is not schema:
                # What the rest of the schema describes usually already clears its `not` / `if`
                # guards, and describing beats filtering: the value stays as small as the rest allows.
                candidate = self._generate_around_conditionals(schema, described)
                if candidate is not NOT_SET:
                    return candidate
        # Prefer spec-declared concrete values when valid: example > examples[0] > default.
        # Surfaces author intent into recursively-generated templates; without this, nested
        # properties whose schemas declare `example`/`default` get synthetic Hypothesis values.
        if isinstance(schema, dict):
            example = schema.get("example", NOT_SET)
            if example is not NOT_SET:
                accepted = _accept_spec_value(example, schema, self)
                if accepted is not NOT_SET:
                    return accepted
            examples = schema.get("examples")
            if isinstance(examples, list):
                for candidate in examples:
                    accepted = _accept_spec_value(candidate, schema, self)
                    if accepted is not NOT_SET:
                        return accepted
            default = schema.get("default", NOT_SET)
            if default is not NOT_SET:
                accepted = _accept_spec_value(default, schema, self)
                if accepted is not NOT_SET:
                    return accepted
        keys = sorted([k for k in schema if not k.startswith("x-") and k not in ANNOTATION_KEYWORDS])
        if keys == ["type"]:
            return cached_draw(get_strategy_for_type(schema["type"]))
        if keys == ["format", "type"]:
            if schema["type"] != "string":
                return cached_draw(get_strategy_for_type(schema["type"]))
            fmt = schema["format"]
            if fmt in self.custom_formats:
                return cached_draw(self.custom_formats[fmt])
        if (
            "properties" in keys
            and set(keys) <= {"properties", "required", "type", "minProperties"}
            and schema.get("type", "object") == "object"
        ):
            obj = {}
            properties = schema["properties"]
            for key, sub_schema in properties.items():
                if isinstance(sub_schema, dict) and "const" in sub_schema:
                    obj[key] = sub_schema["const"]
                else:
                    try:
                        obj[key] = self.generate_from_schema(sub_schema)
                    except Unsatisfiable:
                        pass
            for key in schema.get("required", []):
                if key not in properties:
                    try:
                        obj[key] = self.generate_from_schema({})
                    except Unsatisfiable:
                        pass
            if any(key not in obj for key in schema.get("required", [])):
                raise Unsatisfiable
            # Properties that cannot be generated leave the object short of `minProperties`;
            # names the schema does not mention carry the rest.
            names = ("x" * size for size in count())
            while len(obj) < schema.get("minProperties", 0):
                obj.setdefault(next(names), None)
            return obj
        if "enum" in schema:
            enum_values = [v for v in schema["enum"] if _is_valid_with_formats(v, schema, self)]
            if not enum_values:
                raise Unsatisfiable
            return cached_draw(st.sampled_from(enum_values))
        if "pattern" in schema and "string" in get_type(schema):
            pattern = schema["pattern"]
            try:
                re.compile(pattern)
            except re.error:
                raise Unsatisfiable from None
            if self.location == ParameterLocation.PATH and pattern_requires_literal(pattern, "/{}"):
                raise Unsatisfiable
            min_length = schema.get("minLength")
            max_length = schema.get("maxLength")
            if min_length is not None or max_length is not None:
                pattern_min, pattern_max = pattern_length_bounds(pattern)
                if max_length is not None and max_length < pattern_min:
                    raise Unsatisfiable
                if min_length is not None and pattern_max is not None and min_length > pattern_max:
                    raise Unsatisfiable
                # A floor above the sizes the pattern emits on its own is where drawing and
                # discarding never lands, so the length gets worked out rather than searched for.
                doomed = min_length is not None and min_length > pattern_min
                if doomed and pattern_length_is_unreachable(pattern, min_length, max_length):
                    raise Unsatisfiable
                updated = pattern
                if self.update_pattern is not None:
                    updated = self.update_pattern(pattern, min_length, max_length)
                if doomed and not _keeps_length_within(updated, min_length, max_length):
                    # The quantifier rewrite left the window open, so one length gets spelled into
                    # the pattern outright; shapes it cannot rewrite keep whatever it managed.
                    pinned = pin_pattern_length(pattern, min_length, max_length)
                    if pinned != pattern:
                        updated = pinned
                pattern = updated
            if min_length is not None and min_length > MAX_GENERATED_PATTERN_LENGTH:
                return self._long_string_matching(schema, min_length)
            fmt = schema.get("format")
            strategy = _pattern_strategy(pattern, min_length, max_length, fmt if fmt in VALIDATED_FORMATS else None)
            if strategy is None:
                raise Unsatisfiable from None
            return cached_draw(strategy)
        min_properties = schema.get("minProperties")
        if (
            isinstance(min_properties, int)
            and min_properties > MAX_DRAWN_OBJECT_PROPERTIES
            and "object" in get_type(schema)
        ):
            # Past the generation buffer there is no object worth building.
            if min_properties > INTERNAL_BUFFER_SIZE:
                raise Unsatisfiable
            # Synthesized names only go where any name is admitted and takes the same value.
            if (
                schema.get("additionalProperties", True) is not False
                and "propertyNames" not in schema
                and "patternProperties" not in schema
            ):
                return self._filled_object(schema, min_properties)
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and min_items > MAX_DRAWN_ARRAY_ITEMS and "array" in get_type(schema):
            # Past the generation buffer there is no array worth building.
            if min_items > INTERNAL_BUFFER_SIZE:
                raise Unsatisfiable
            items = schema.get("items", True)
            # Repeating one element cannot satisfy either of these.
            if (
                (items is True or isinstance(items, dict))
                and not schema.get("uniqueItems")
                and "contains" not in schema
            ):
                return self._tiled_array(schema, min_items)
        if (
            (keys == ["items", "type"] or keys == ["items", "minItems", "type"])
            and isinstance(schema["items"], dict)
            and "array" in get_type(schema)
        ):
            items = schema["items"]
            min_items = schema.get("minItems", 0)
            if "enum" in items:
                enum_values = [v for v in items["enum"] if _is_valid_with_formats(v, items, self)]
                if not enum_values:
                    # Nothing matches, so only an empty array can conform.
                    if min_items:
                        raise Unsatisfiable
                    return []
                return cached_draw(st.lists(st.sampled_from(enum_values), min_size=min_items))
            # Recurse so `items`-level `example`/`examples`/`default` reach generation.
            if any(k in items for k in ("example", "examples", "default")):
                size = max(min_items, 1)
                return [self.generate_from_schema(items) for _ in range(size)]
            sub_keys = sorted([k for k in items if not k.startswith("x-") and k not in ["description", "example"]])
            if sub_keys == ["type"] and items["type"] == "string":
                return cached_draw(st.lists(st.text(), min_size=min_items))
            if (
                sub_keys == ["properties", "required", "type"]
                or sub_keys == ["properties", "type"]
                or sub_keys == ["properties"]
            ):
                strategies = {key: self.build_strategy(sub) for key, sub in items["properties"].items()}
                if all(strategy is not None for strategy in strategies.values()):
                    return cached_draw(
                        st.lists(
                            st.fixed_dictionaries(cast("dict[str, st.SearchStrategy]", strategies)),
                            min_size=min_items,
                        )
                    )

        if keys == ["allOf"]:
            # Resolve refs into a fresh list so the caller's schema is not mutated; the
            # validator cache relies on schemas remaining structurally stable after first use.
            references = [item["$ref"] for item in schema["allOf"] if isinstance(item, dict) and "$ref" in item]
            if any(self.is_exhausted(reference, counters=self.generating) for reference in references):
                raise Unsatisfiable
            resolved_all_of = [
                self.resolve_ref(item["$ref"]) if isinstance(item, dict) and "$ref" in item else item
                for item in schema["allOf"]
            ]
            merged = _merge_all_of({**schema, "allOf": resolved_all_of})
            if merged is not None:
                # Resolving above leaves no pointer to count, so the branches stay counted while the
                # value they lead to is built - a branch pointing back here would never bottom out.
                with ExitStack() as stack:
                    for reference in references:
                        stack.enter_context(self.expand(reference, counters=self.generating))
                    return self.generate_from_schema(merged)
            schema = {**schema, "allOf": resolved_all_of}

        if isinstance(schema, dict) and "examples" in schema:
            # Examples may contain binary data, which canonicalization rejects
            schema = {key: value for key, value in schema.items() if key != "examples"}
        # Deep clone so the pattern rewrite below leaves the original schema alone
        cloned = deepclone(schema)
        # Add bundled schemas if any; they are shared and reshaped once, not cloned per value.
        if isinstance(cloned, dict) and BUNDLE_STORAGE_KEY in self.root_schema:
            cloned[BUNDLE_STORAGE_KEY] = self.root_schema[BUNDLE_STORAGE_KEY]
        strategy = self.build_strategy(cloned)
        if strategy is None and isinstance(cloned, dict):
            # An optional property nothing can be drawn for otherwise takes every value of the object with it.
            reduced = self._without_unbuildable_optional_properties(cloned)
            if reduced is not None:
                strategy = self.build_strategy(reduced)
        if strategy is None:
            raise Unsatisfiable
        # Keep generation consistent with the validator draft semantics used by this operation.
        # This avoids producing positive values that the validator for the same schema would reject.
        if (
            isinstance(schema, dict)
            and (fmt := schema.get("format")) in VALIDATED_FORMATS
            and fmt in self.custom_formats
        ):
            validator = _get_format_validator(fmt, self.validator_cls)
            strategy = strategy.filter(lambda v: not isinstance(v, str) or validator.is_valid(v))
        return self.generate_from(strategy)


def _update_schema_pattern(
    schema: dict[str, Any], update_pattern: Callable[[str, int | None, int | None], str]
) -> None:
    pattern = schema.get("pattern")
    # Meta-schemas (e.g. Kubernetes CRD `JSONSchemaProps`) carry property *names*
    # `pattern` / `minLength` / `maxLength` whose values are sub-schema dicts; skip
    # optimization unless these slots actually hold a regex string and integer bounds.
    if not isinstance(pattern, str) or not pattern:
        return
    min_length = schema.get("minLength")
    max_length = schema.get("maxLength")
    if not isinstance(min_length, int) or isinstance(min_length, bool):
        min_length = None
    if not isinstance(max_length, int) or isinstance(max_length, bool):
        max_length = None
    if min_length or max_length:
        new_pattern = update_pattern(pattern, min_length, max_length)
        if new_pattern != pattern:
            apply_rewritten_pattern(schema, new_pattern, min_length, max_length)


def _apply_pattern_optimizations(
    obj: object, update_pattern: Callable[[str, int | None, int | None], str] | None
) -> None:
    if update_pattern is None:
        return
    if isinstance(obj, dict):
        _update_schema_pattern(obj, update_pattern)
        for value in obj.values():
            _apply_pattern_optimizations(value, update_pattern)
    elif isinstance(obj, list):
        for item in obj:
            _apply_pattern_optimizations(item, update_pattern)


_READY_BUNDLE_CACHE: BoundedCache = BoundedCache(maxsize=64)


def _ready_bundle(
    bundle: dict[str, Any], update_pattern: Callable[[str, int | None, int | None], str] | None, draft4: bool
) -> dict[str, Any]:
    """The bundled definitions with pattern rewrites and draft spellings already applied."""
    key = (id(bundle), id(update_pattern), draft4)
    cached = _READY_BUNDLE_CACHE.get(key)
    if cached is not MISSING:
        return cached[0]
    if update_pattern is None:
        result = _prepared_by_name(bundle, draft4=draft4, drop=frozenset())
    else:
        # Rewriting in place, so on a copy the caller's document does not share.
        result = deepclone(bundle)
        _apply_pattern_optimizations(result, update_pattern)
        result = _prepared_by_name(result, draft4=draft4, drop=frozenset())
    # The trailing elements pin the keyed objects, so their `id`s cannot be recycled into a stale hit.
    _READY_BUNDLE_CACHE[key] = (result, bundle, update_pattern)
    return result


T = TypeVar("T")


if c_make_encoder is not None:
    _iterencode = c_make_encoder(None, None, encode_basestring_ascii, None, ":", ",", True, False, False)
elif _make_iterencode is not None:
    _iterencode = _make_iterencode(
        None, None, encode_basestring_ascii, None, float.__repr__, ":", ",", True, False, True
    )
else:
    encoder = JSONEncoder(skipkeys=False, sort_keys=False, indent=None, separators=(":", ","))
    _iterencode = encoder.iterencode


def _encode(o: Any) -> str:
    return "".join(_iterencode(o, False))


def _convert_bytes_for_hashing(value: Any) -> Any:
    """Convert bytes/non-string keys to a hashable string representation for JSON encoding."""
    if isinstance(value, bytes):
        return f"__bytes__:{value.hex()}"
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else str(k)): _convert_bytes_for_hashing(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert_bytes_for_hashing(v) for v in value]
    return value


_MAX_INLINE_KEY = 512


def _digest(serialized: str) -> str | bytes:
    # Keys answer membership only, so a long serialized form is dead weight - tens of KB per entry on nested bodies.
    # Short ones stay inline; hashing them costs more than keeping them. A 128-bit collision is rare.
    if len(serialized) <= _MAX_INLINE_KEY:
        return serialized
    return blake2b(serialized.encode(), digest_size=16).digest()


def _reads_references(value: object) -> bool:
    if isinstance(value, dict):
        return "$ref" in value or any(_reads_references(item) for item in value.values())
    if isinstance(value, list):
        return any(_reads_references(item) for item in value)
    return False


def _to_hashable_key(value: T, _encode: Callable = _encode) -> tuple[type, str | bytes | T]:
    if type(value) is dict or type(value) is list:
        # Plain JSON-shaped containers (the common case) canonicalize in Rust without
        # an intermediate Python-side deep-copy. Bytes inside the value reject the
        # native call; fall back to the bytes-aware path.
        try:
            return type(value), _digest(jsonschema_rs.canonical.json.to_string(value))
        except (TypeError, ValueError):
            pass
        converted = _convert_bytes_for_hashing(value)
        serialized = _encode(converted)
        return type(value), _digest(serialized)
    return type(value), value


class HashSet:
    """Helper to track already generated values."""

    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: set[tuple] = set()

    def __bool__(self) -> bool:
        return bool(self._data)

    def __contains__(self, value: Any) -> bool:
        return _to_hashable_key(value) in self._data

    def insert(self, value: Any) -> bool:
        key = _to_hashable_key(value)
        before = len(self._data)
        self._data.add(key)
        return len(self._data) > before

    def clear(self) -> None:
        self._data.clear()


_COMBINATOR_KEYS = frozenset({"anyOf", "oneOf", "allOf", "not", "if", "then", "else"})
# Keywords that rule values out rather than describe them; generation works around them.
_CONDITIONAL_KEYS = frozenset({"not", "if", "then", "else"})
# Keywords holding data rather than sub-schemas; their contents are values, not spellings to rewrite.
_DATA_KEYWORDS = frozenset({"const", "enum", "default", "example", "examples"})
# Keywords whose value maps names to schemas.
_NAMED_SCHEMAS = frozenset(
    {"properties", "patternProperties", "definitions", "$defs", "dependentSchemas", BUNDLE_STORAGE_KEY}
)


def _judge(schema: JsonSchema, ctx: CoverageContext) -> Callable[[Any], bool] | None:
    """Whether the schema admits a value, or `None` where nothing can check it."""
    judges = _judges(schema, ctx)
    if not judges:
        return None
    return lambda value: all(judge.is_valid(value) for judge in judges)


def _prepared(value: Any, *, draft4: bool = False, drop: frozenset[str] = frozenset()) -> Any:
    """`value` without the dropped keywords, and without spellings Draft 4 rejects."""
    if isinstance(value, list):
        items = [_prepared(item, draft4=draft4, drop=drop) for item in value]
        return value if all(new is old for new, old in zip(items, value, strict=True)) else items
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    changed = False
    for key, sub in value.items():
        # Draft 4 spells "nothing is required" as the absent keyword and rejects the empty list.
        if key in drop or (draft4 and key == "required" and sub == []):
            changed = True
            continue
        if draft4 and key == "const":
            # Draft 4 has no `const`; a one-value `enum` is how it pins a value.
            result["enum"] = [sub]
            changed = True
            continue
        if draft4 and key in ("anyOf", "oneOf", "allOf") and any(isinstance(branch, bool) for branch in sub):
            # Draft 4 has no boolean schemas either.
            sub = [{} if branch is True else {"not": {}} if branch is False else branch for branch in sub]
            changed = True
        if key in _DATA_KEYWORDS:
            prepared_sub = sub
        elif key in _NAMED_SCHEMAS and isinstance(sub, dict):
            prepared_sub = _prepared_by_name(sub, draft4=draft4, drop=drop)
        else:
            prepared_sub = _prepared(sub, draft4=draft4, drop=drop)
        changed = changed or prepared_sub is not sub
        result[key] = prepared_sub
    return result if changed else value


def _prepared_by_name(schemas: dict[str, Any], *, draft4: bool, drop: frozenset[str]) -> dict[str, Any]:
    """`_prepared` over a map whose keys are names, not keywords, so a property called `not` stays."""
    result = {name: _prepared(sub, draft4=draft4, drop=drop) for name, sub in schemas.items()}
    return schemas if all(new is old for new, old in zip(result.values(), schemas.values(), strict=True)) else result


def _without_conditionals(schema: JsonSchemaObject) -> JsonSchemaObject | None:
    """A wider schema without the keywords generation cannot follow, or `None` if there are none."""
    base = _prepared(schema, drop=_CONDITIONAL_KEYS)
    if base is schema:
        return None
    condition = schema.get("if")
    if condition is None:
        return base
    # Each conditional branch on its own, so the constrained shapes stay reachable.
    branches: list[JsonSchema] = []
    if "then" in schema:
        branches.append({"allOf": [base, condition, schema["then"]]})
    if "else" in schema:
        branches.append({"allOf": [base, schema["else"]]})
    branches.append(base)
    return {"anyOf": branches}


# Keywords that describe rather than constrain; a second, different one changes nothing.
_ANNOTATION_KEYWORDS = frozenset(
    {
        "$comment",
        "default",
        "deprecated",
        "description",
        "discriminator",
        "example",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
        "xml",
    }
)
_TIGHTEST_LOWER = frozenset({"minLength", "minItems", "minProperties", "minimum", "exclusiveMinimum"})
_TIGHTEST_UPPER = frozenset({"maxLength", "maxItems", "maxProperties", "maximum", "exclusiveMaximum"})


def _merge_all_of(schema: JsonSchemaObject) -> JsonSchemaObject | None:
    """`allOf` folded into the schema around it, or `None` when a branch cannot be folded."""
    branches = schema.get("allOf")
    if not isinstance(branches, list):
        return None
    outer = {key: value for key, value in schema.items() if key != "allOf"}
    merged = dict(outer)
    folded_branches = [outer]
    for branch in branches:
        if isinstance(branch, dict) and "allOf" in branch:
            folded = _merge_all_of(branch)
            if folded is None:
                return None
            branch = folded
        if not isinstance(branch, dict):
            return None
        folded_branches.append(branch)
        for key, value in branch.items():
            if not _merge_keyword(merged, key, value):
                return None
    if merged.get("type") == [] or merged.get("enum") == []:
        # The branches leave no type or no value in common, so nothing satisfies all of them.
        return {"not": {}}
    if merged.get("not") == {}:
        # A branch rejects every value, so the keywords folded in around it cannot make one fit.
        return {"not": {}}
    if "$ref" in merged and any(key != "$ref" and key not in _ANNOTATION_KEYWORDS for key in merged):
        # A reference that stays unresolved overrides everything folded in beside it, so those
        # constraints would silently vanish from the value.
        return None
    merged_names = set(merged.get("properties", {}))
    for branch in folded_branches:
        extra = branch.get("additionalProperties")
        # A branch judging every name it does not declare still judges the names its siblings
        # declare; folding the property sets together would let those escape it.
        if isinstance(extra, dict) and extra and merged_names - set(branch.get("properties", {})):
            return None
    _restrict_closed_properties(merged, folded_branches)
    # Keyword order drives the order coverage walks constraints in; keep it independent of
    # which branch each one came from.
    return dict(sorted(merged.items()))


def _restrict_closed_properties(merged: dict[str, Any], branches: list[JsonSchemaObject]) -> None:
    """Drop properties that a branch forbidding extras does not name; no value could carry them."""
    allowed: set[str] | None = None
    for branch in branches:
        if branch.get("additionalProperties") is not False or branch.get("patternProperties"):
            continue
        names = set(branch.get("properties", {}))
        allowed = names if allowed is None else allowed & names
    if allowed is None:
        return
    properties = merged.get("properties")
    if isinstance(properties, dict):
        merged["properties"] = {name: sub for name, sub in properties.items() if name in allowed}
    required = merged.get("required")
    if isinstance(required, list):
        merged["required"] = [name for name in required if name in allowed]


def _merge_keyword(merged: dict[str, Any], key: str, value: Any) -> bool:
    """Add one keyword to the merged schema; `False` when the two spellings cannot be folded into one."""
    current = merged.get(key, NOT_SET)
    if key in ("const", "enum"):
        _merge_allowed_values(merged, key, value)
    elif current is NOT_SET:
        merged[key] = value
    elif key in _TIGHTEST_LOWER and _is_number(current) and _is_number(value):
        merged[key] = max(current, value)
    elif key in _TIGHTEST_UPPER and _is_number(current) and _is_number(value):
        merged[key] = min(current, value)
    elif key == "required" and isinstance(current, list) and isinstance(value, list):
        merged[key] = list(dict.fromkeys(current + value))
    elif key == "type":
        merged[key] = _intersect_types(current, value)
    elif key == "properties" and isinstance(current, dict) and isinstance(value, dict):
        properties = dict(current)
        for name, sub_schema in value.items():
            if name in properties:
                folded = _merge_all_of({"allOf": [properties[name], sub_schema]})
                if folded is None:
                    return False
                properties[name] = folded
            else:
                properties[name] = sub_schema
        merged[key] = properties
    elif current != value and key not in _ANNOTATION_KEYWORDS:
        # Two constraints on the same keyword, e.g. a pair of formats. Both hold, and folding
        # would keep only one of them.
        return False
    return True


def _merge_allowed_values(merged: dict[str, Any], key: str, value: Any) -> None:
    """`const` and `enum` both name the values a schema allows; keep the ones both sides allow."""
    incoming = [value] if key == "const" else value
    pinned = "const" in merged or key == "const"
    if "const" in merged:
        current = [merged["const"]]
    elif "enum" in merged:
        current = merged["enum"]
    else:
        merged[key] = value
        return
    if not isinstance(current, list) or not isinstance(incoming, list):
        return
    shared = [item for item in current if item in incoming]
    merged.pop("const", None)
    merged.pop("enum", None)
    if len(shared) == 1 and pinned:
        merged["const"] = shared[0]
    else:
        merged["enum"] = shared


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _intersect_types(current: Any, value: Any) -> Any:
    current_types = current if isinstance(current, list) else [current]
    value_types = value if isinstance(value, list) else [value]
    shared = [name for name in current_types if name in value_types]
    return shared[0] if len(shared) == 1 else shared


def _with_effective_required(schema: JsonSchemaObject) -> JsonSchemaObject:
    existing_required: list[str] = schema.get("required", [])
    properties = schema.get("properties", {})
    if not properties:
        return schema
    for key in ("anyOf", "oneOf"):
        sub_schemas = schema.get(key)
        if sub_schemas:
            for sub_schema in sub_schemas:
                if isinstance(sub_schema, dict) and "required" in sub_schema:
                    extra = [f for f in sub_schema["required"] if f not in existing_required and f in properties]
                    if extra:
                        return {**schema, "required": list(existing_required) + extra}
                    break
    return schema


def _resolve_sub_schema(ctx: CoverageContext, sub: JsonSchema) -> JsonSchema:
    """Resolve a $ref sub-schema to its concrete content before merging."""
    if not isinstance(sub, dict) or "$ref" not in sub:
        return sub
    try:
        resolved = ctx.resolve_ref(sub["$ref"])
        if not isinstance(resolved, dict):
            return resolved
        # Deep-merge so sibling `properties`/`required` augment the resolved schema
        # rather than wiping it; the discriminator-pinning rewrite produces exactly
        # this shape (sibling pins the discriminator key, resolved carries the rest).
        merged = {**resolved}
        for key, value in sub.items():
            if key == "$ref":
                continue
            if key == "properties" and isinstance(value, dict) and isinstance(merged.get("properties"), dict):
                merged["properties"] = {**merged["properties"], **value}
            elif key == "required" and isinstance(value, list) and isinstance(merged.get("required"), list):
                merged["required"] = list(dict.fromkeys(merged["required"] + value))
            else:
                merged[key] = value
        return merged
    except RefResolutionError:
        # Schemas are bundled, so this should not happen in practice
        return sub


def _has_array_sibling(sub_schemas: list) -> bool:
    for sub in sub_schemas:
        if isinstance(sub, dict):
            ty = sub.get("type")
            if ty == "array" or (isinstance(ty, list) and "array" in ty):
                return True
    return False


def _merge_with_parent_context(parent: JsonSchemaObject, sub: JsonSchema) -> JsonSchema:
    if not isinstance(sub, dict):
        return sub
    result: dict[str, Any] = {
        k: (deepclone(v) if k == "properties" else v) for k, v in parent.items() if k not in _COMBINATOR_KEYS
    }
    for key, value in sub.items():
        if key == "required" and "required" in result:
            parent_req: list[str] = result["required"] if isinstance(result["required"], list) else [result["required"]]
            sub_req: list[str] = value if isinstance(value, list) else [value]
            result["required"] = list(dict.fromkeys(parent_req + sub_req))
        elif key == "properties" and "properties" in result:
            result["properties"] = {**result["properties"], **value}
        else:
            result[key] = value
    return result


def _generate_oversized_string(
    ctx: CoverageContext, original_schema: JsonSchemaObject, new_schema: dict[str, Any], target_length: int
) -> str | None:
    pattern = new_schema.get("pattern")
    if not isinstance(pattern, str):
        try:
            return ctx.generate_from_schema(new_schema)
        except (InvalidArgument, Unsatisfiable):
            # Format constrains the length (e.g. uuid is fixed at 36); synthesize a plain
            # string that violates maxLength regardless.
            if target_length < MAX_STRING_LENGTH:
                return "a" * target_length
            return None
    try:
        if target_length - 1 > NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN:
            # Pattern combined with a large length is too slow; drop it.
            return ctx.generate_from_schema({k: v for k, v in new_schema.items() if k != "pattern"})
        # Hand the pattern over as it stands. Working the length into it happens during generation,
        # where the sizes the pattern reaches on its own are still in view - which is what tells an
        # ambiguous rewrite apart from one that already lands on a single length.
        return ctx.generate_from_schema(new_schema)
    except (InvalidArgument, Unsatisfiable):
        # Pattern intrinsically unsatisfiable: synthesize a fixed-length string so the
        # maxLength rule still fires even though the value also violates the pattern.
        # Only do it within the negative-fuzzing pattern cap to avoid shipping huge payloads.
        if target_length <= NEGATIVE_MODE_MAX_LENGTH_WITH_PATTERN + 1:
            return "a" * target_length
        return None


def _generate_template_with_deflation_fallback(
    ctx: CoverageContext, schema: JsonSchemaObject, template_schema: JsonSchemaObject
) -> Any:
    try:
        return ctx.generate_from_schema(template_schema)
    except Unsatisfiable:
        # `_get_template_schema` may promote optionals to required for completeness; one
        # unsatisfiable optional then sinks the whole template. Retry with only the
        # schema's original required so the per-property sweep can still emit each
        # property individually.
        original_required = schema.get("required", []) if isinstance(schema, dict) else []
        properties = template_schema.get("properties", {}) if isinstance(template_schema, dict) else {}
        deflated = {
            **template_schema,
            "required": [k for k in original_required if properties.get(k) != {"not": {}}],
        }
        return ctx.generate_from_schema(deflated)


def _ensure_contains_bounds(ctx: CoverageContext, value: list, schema: JsonSchemaObject) -> list:
    # Generation honors `contains` (>= 1 match) but not `minContains`/`maxContains`; bring the
    # match count within bounds by adding matches or replacing surplus ones with non-matching items.
    contains = schema["contains"]
    min_contains = schema.get("minContains", 1)
    max_contains = schema.get("maxContains")
    matching = [index for index, item in enumerate(value) if is_valid(item, contains)]
    result = list(value)
    if len(matching) < min_contains:
        max_items = schema.get("maxItems")
        non_matching = [index for index, item in enumerate(result) if not is_valid(item, contains)]
        while len(matching) < min_contains and (non_matching or max_items is None or len(result) < max_items):
            candidate = ctx.generate_from_schema(contains)
            if non_matching:
                index = non_matching.pop()
                result[index] = candidate
            else:
                result.append(candidate)
                index = len(result) - 1
            matching.append(index)
    elif max_contains is not None and len(matching) > max_contains:
        items = schema.get("items")
        filler = {"allOf": [items, {"not": contains}]} if isinstance(items, dict) else {"not": contains}
        for index in matching[max_contains:]:
            result[index] = ctx.generate_from_schema(filler)
    return result


def _cover_positive_for_type(
    ctx: CoverageContext, schema: JsonSchemaObject, ty: str | None, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    # In negative-only mode this function never yields values.
    # Avoid expensive template generation in that case.
    if GenerationMode.POSITIVE not in ctx.generation_modes:
        return

    if ty == "object" or ty == "array":
        template_schema = _get_template_schema(schema, ty, ctx)
        template = _generate_template_with_deflation_fallback(ctx, schema, template_schema)
    elif _implies_object_type(schema):
        template_schema = _get_template_schema(schema, "object", ctx)
        template = _generate_template_with_deflation_fallback(ctx, schema, template_schema)
    elif _implies_array_type(schema):
        template_schema = _get_template_schema(schema, "array", ctx)
        template = _generate_template_with_deflation_fallback(ctx, schema, template_schema)
    else:
        template = None
    if GenerationMode.POSITIVE in ctx.generation_modes:
        ctx = ctx.with_positive()
        enum = schema.get("enum", NOT_SET)
        const = schema.get("const", NOT_SET)
        for key in ("anyOf", "oneOf"):
            sub_schemas = schema.get(key)
            if sub_schemas is not None:
                if key == "oneOf":
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in sub_schemas
                    ]
                    one_of_validators: list[jsonschema_rs.Validator] | None = _make_branch_validators(
                        resolved_schemas, ctx
                    )
                else:
                    one_of_validators = None
                # A branch may generate values that satisfy the branch schema but violate
                # parent-level constraints (e.g. parent `properties.discriminator: const value`,
                # or parent `type` excluding the branch's type). Only gate body schemas:
                # header/query/cookie/path adapters inject type:string for serialization, which
                # would incorrectly filter null values from nullable anyOf branches.
                parent_validator: jsonschema_rs.Validator | None = None
                if ctx.location == ParameterLocation.BODY and any(
                    key in ALL_KEYWORDS and key not in _COMBINATOR_KEYS for key in schema
                ):
                    # Nested `$ref`s point into the root's bundle; without it the validator
                    # fails to compile and branch values pass unfiltered.
                    full_schema: JsonSchema = schema
                    if BUNDLE_STORAGE_KEY in ctx.root_schema:
                        full_schema = {**schema, BUNDLE_STORAGE_KEY: ctx.root_schema[BUNDLE_STORAGE_KEY]}
                    try:
                        parent_validator = make_validator(full_schema, ctx.validator_cls)
                    except Exception:
                        pass
                # For non-body params, an empty bare string serializes to the same wire form as an
                # empty array (`?p=`), so the string branch never disambiguates from a sibling array
                # branch. Force non-empty strings.
                disambiguate_string_branch = (
                    ctx.location != ParameterLocation.BODY
                    and isinstance(sub_schemas, list)
                    and _has_array_sibling(sub_schemas)
                )
                for idx, sub_schema in enumerate(sub_schemas):
                    # Resolving the branch here leaves no pointer for the walk to count, so a branch
                    # leading back into the value being covered has to be counted before it is opened.
                    reference = sub_schema.get("$ref") if isinstance(sub_schema, dict) else None
                    if reference is not None and ctx.is_exhausted(reference):
                        continue
                    effective = _resolve_sub_schema(ctx, sub_schema)
                    if (
                        disambiguate_string_branch
                        and isinstance(effective, dict)
                        and effective.get("type") == "string"
                        and "minLength" not in effective
                    ):
                        effective = {**effective, "minLength": 1}
                    if isinstance(effective, dict) and "properties" in effective:
                        # See GH-3584
                        # Sub-schema defines its own properties — treat as a complete type, do not inject parent properties.
                        # Exception: required fields absent from the branch but defined in the parent must be injected
                        # (or referenced from the branch's own properties) so they are still honoured.
                        parent_props = schema.get("properties", {}) if isinstance(schema, dict) else {}
                        parent_required_raw = schema.get("required", []) if isinstance(schema, dict) else []
                        parent_required: list = parent_required_raw if isinstance(parent_required_raw, list) else []
                        branch_props = effective.get("properties", {})
                        branch_required_raw = effective.get("required", [])
                        branch_required: list = branch_required_raw if isinstance(branch_required_raw, list) else []
                        to_inject = {
                            f: parent_props[f]
                            for f in set(branch_required) | set(parent_required)
                            if f not in branch_props and f in parent_props
                        }
                        # Required keys the branch should honour: its own plus parent-required keys
                        # that the branch can already satisfy (or that we inject above).
                        merged_required = list(
                            dict.fromkeys(
                                list(branch_required)
                                + [
                                    f
                                    for f in parent_required
                                    if f not in branch_required and (f in branch_props or f in to_inject)
                                ]
                            )
                        )
                        if to_inject or merged_required != branch_required:
                            effective = {
                                **effective,
                                "properties": {**branch_props, **to_inject} if to_inject else branch_props,
                                "required": merged_required,
                            }
                        gen = cover_schema_iter(ctx, effective)
                    else:
                        # See GH-3520
                        # Additive constraint — merge parent context so sub-schema knows field definitions
                        gen = cover_schema_iter(ctx, _merge_with_parent_context(schema, effective))
                    with ctx.expand(reference) if reference is not None else nullcontext():
                        if one_of_validators is not None:
                            # Only yield values valid for exactly this one branch
                            for v in gen:
                                if not is_valid_for_others(v.value, idx, one_of_validators):
                                    if parent_validator is None or (
                                        not contains_binary(v.value) and parent_validator.is_valid(v.value)
                                    ):
                                        yield v
                        else:
                            for v in gen:
                                if parent_validator is None or (
                                    not contains_binary(v.value) and parent_validator.is_valid(v.value)
                                ):
                                    yield v
        all_of = schema.get("allOf")
        # Set when the merged form is used for allOf: the canonical schema covers the full merged
        # constraints, so the outer schema's type/properties generation must be skipped to avoid
        # producing cases that violate allOf's required fields.
        allof_handles_all = False
        if all_of is not None:
            # Covering a lone branch on its own drops every constraint sitting beside `allOf`, so that
            # shortcut only holds when the outer schema carries none of them.
            outer_constrains = any(key != "allOf" and key in ALL_KEYWORDS for key in schema)
            if isinstance(all_of, list) and len(all_of) == 1 and not outer_constrains:
                yield from cover_schema_iter(ctx, all_of[0])
            else:
                folded = False
                inlined = schema
                with suppress(jsonschema_rs.ValidationError):
                    inlined, inlined_refs = _inline_allof_refs(schema, ctx)
                    merged = _merge_all_of(inlined)
                    if merged is not None:
                        with ExitStack() as stack:
                            for reference in inlined_refs:
                                stack.enter_context(ctx.expand(reference))
                            yield from cover_schema_iter(ctx, merged)
                        folded = True
                if not folded:
                    # Members with no single flat spelling (two `pattern`s, two `format`s) leave the
                    # walker nothing to cover; emit one conforming value instead of dropping the schema.
                    with suppress(Unsatisfiable):
                        yield PositiveValue(
                            ctx.generate_from_schema(inlined),
                            scenario=CoverageScenario.DEFAULT_POSITIVE_TEST,
                            description="Valid value",
                        )
                allof_handles_all = True
        if not allof_handles_all:
            if enum is not NOT_SET:
                for value in enum:
                    if _is_valid_with_formats(value, schema, ctx):
                        yield PositiveValue(value, scenario=CoverageScenario.ENUM_VALUE, description="Enum value")
            elif const is not NOT_SET:
                if _is_valid_with_formats(const, schema, ctx):
                    yield PositiveValue(const, scenario=CoverageScenario.CONST_VALUE, description="Const value")
            elif ty is not None or _implies_object_type(schema) or _implies_array_type(schema):
                yield from _filter_against_combinators(
                    _positive_for_describing_keywords(ctx, schema, ty, template), schema, ctx
                )
        if "not" in schema and isinstance(schema["not"], dict | bool):
            # For 'not' schemas: generate negative cases of inner schema (violations)
            # These violations are positive for the outer schema, so flip the mode.
            # The inner-violation alone doesn't guarantee the value satisfies the outer's
            # other constraints (type, properties, etc.); validate before yielding.
            nctx = ctx.with_negative()
            for flipped in _flip_generation_mode_for_not(cover_schema_iter(nctx, schema["not"], seen)):
                if flipped.generation_mode == GenerationMode.POSITIVE and not _is_valid_with_formats(
                    flipped.value, schema, ctx
                ):
                    continue
                yield flipped


def _inline_allof_refs(schema: dict, ctx: CoverageContext, seen: frozenset[str] = frozenset()) -> tuple[dict, set[str]]:
    # Resolve refs before merging so required fields from $ref-only siblings survive. Never writes to the input
    # (it shares sub-schemas with the root document); the caller counts the returned inlined refs as expansions.
    all_of = schema.get("allOf")
    if not all_of:
        return schema, set()
    new_all_of = []
    inlined_refs: set[str] = set()
    changed = False
    for sub_schema in all_of:
        if isinstance(sub_schema, dict) and "$ref" in sub_schema:
            ref = sub_schema["$ref"]
            if ref not in seen and not ctx.is_exhausted(ref):
                resolved = deepclone(ctx.resolve_ref(ref))
                inlined_refs.add(ref)
                if isinstance(resolved, dict):
                    resolved, nested = _inline_allof_refs(resolved, ctx, seen | {ref})
                    inlined_refs |= nested
                new_all_of.append(resolved)
                changed = True
            else:
                new_all_of.append(sub_schema)
        elif isinstance(sub_schema, dict):
            inlined, nested = _inline_allof_refs(sub_schema, ctx, seen)
            changed = changed or inlined is not sub_schema
            inlined_refs |= nested
            new_all_of.append(inlined)
        else:
            new_all_of.append(sub_schema)
    if not changed:
        return schema, inlined_refs
    return {**schema, "allOf": new_all_of}, inlined_refs


@contextmanager
def _ignore_unfixable(
    *,
    ref_error: type[Exception] = RefResolutionError,
) -> Generator:
    try:
        yield
    except GeneratorExit:
        # Interpreter shutdown clears module globals before closing suspended generators, so the
        # clauses below can no longer be evaluated.
        raise
    except (Unsatisfiable, ref_error, jsonschema_rs.ValidationError):
        pass
    except InvalidArgument as exc:
        message = str(exc)
        if "Cannot create non-empty" not in message and "is not in the specified alphabet" not in message:
            raise
    except TypeError as exc:
        if "first argument must be string or compiled pattern" not in str(exc):
            raise


def _pick_property_name(schema: dict, existing_keys: set[str], ctx: CoverageContext, start: int = 0) -> str | None:
    """Return an additional-property key: propertyNames-valid, matching no patternProperties, or None."""
    patterns = _pattern_property_regexes(schema)

    def is_additional(key: object) -> bool:
        # A patternProperties match is validated against that pattern's schema, not
        # `additionalProperties`, so such a key can't carry an additionalProperties violation.
        return isinstance(key, str) and key not in existing_keys and not any(p.search(key) for p in patterns)

    property_names = schema.get("propertyNames")
    if property_names is False:
        # No property name can satisfy `false` — adding any key would be invalid.
        return None
    if isinstance(property_names, dict):
        try:
            # Degenerate schemas (e.g. `{}`) may yield non-strings; skip rather than corrupt.
            key = ctx.generate_from_schema(property_names)
        except Exception:
            return None
        return key if is_additional(key) else None
    fallback = _generate_additional_property_key(existing_keys, start)
    if is_additional(fallback):
        return fallback
    return next((candidate for candidate in _UNEXPECTED_PROPERTY_KEYS[1:] if is_additional(candidate)), None)


def _negation_ignored_by_dialect(ctx: CoverageContext, keyword: str) -> bool:
    # Draft 4 (Swagger 2.0 / Open API 3.0) predates `const` and `propertyNames`; the dialect's
    # validator ignores them, so mutating them cannot produce negative test cases.
    return keyword in ("const", "propertyNames") and ctx.validator_cls is jsonschema_rs.Draft4Validator


def cover_schema_iter(
    ctx: CoverageContext, schema: JsonSchema, seen: HashSet | None = None
) -> Generator[GeneratedValue, None, None]:
    if seen is None:
        seen = HashSet()

    if isinstance(schema, dict) and "$ref" in schema:
        reference = schema["$ref"]
        if ctx.is_exhausted(reference):
            # Going around again would never come back out, and a value covering this position has
            # to satisfy what the pointer names, so there is nothing left to say about it.
            return
        try:
            resolved = ctx.resolve_ref(reference)
            if isinstance(resolved, dict):
                merged = {**resolved}
                for k, v in schema.items():
                    if k == "$ref":
                        continue
                    if k == "properties" and isinstance(v, dict) and isinstance(merged.get("properties"), dict):
                        # Deep-merge: resolved's properties take lower priority than sibling properties,
                        # but both must be present so that 'required' fields from the resolved schema
                        # are included in the merged properties dict.
                        merged["properties"] = {**merged["properties"], **v}
                    elif k == "required" and isinstance(v, list) and isinstance(merged.get("required"), list):
                        merged["required"] = list(dict.fromkeys(merged["required"] + v))
                    else:
                        merged[k] = v
                # Draft 4 silently drops `$ref` siblings; the merged form is what generation
                # walks but the body validator only honors the bare ref target. Build a view
                # validator from the un-merged schema and skip negative values it accepts.
                unmerged_validator: jsonschema_rs.Validator | None = None
                if any(k != "$ref" and k in ALL_KEYWORDS for k in schema):
                    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY) if isinstance(ctx.root_schema, dict) else None
                    check_schema = schema if bundle is None else {**schema, BUNDLE_STORAGE_KEY: bundle}
                    try:
                        unmerged_validator = ctx.validator_cls(check_schema, pattern_options=FANCY_REGEX_OPTIONS)
                    except Exception:
                        pass
                with ctx.expand(reference):
                    for generated in cover_schema_iter(ctx, merged, seen):
                        if (
                            unmerged_validator is not None
                            and generated.generation_mode == GenerationMode.NEGATIVE
                            and not contains_binary(generated.value)
                            and unmerged_validator.is_valid(generated.value)
                        ):
                            continue
                        yield generated
            else:
                with ctx.expand(reference):
                    yield from cover_schema_iter(ctx, resolved, seen)
            return
        except GeneratorExit:
            # Interpreter shutdown clears module globals before closing suspended generators, so the
            # clause below can no longer be evaluated.
            raise
        except RefResolutionError:
            # Can't resolve a reference - at this point, we can't generate anything useful as `$ref` is in the current schema root
            return

    if schema is True:
        types = ["null", "boolean", "string", "number", "array", "object"]
        schema = {}
    elif schema is False:
        types = []
        schema = {"not": {}}
    elif not any(k in ALL_KEYWORDS for k in schema):
        types = ["null", "boolean", "string", "number", "array", "object"]
    else:
        types = schema.get("type", [])
    push_examples_to_properties(schema)
    if not isinstance(types, list):
        types = [types]  # type: ignore[unreachable]
    if not types:
        with _ignore_unfixable():
            yield from _filter_against_not(_cover_positive_for_type(ctx, schema, None), schema, ctx)
    for ty in types:
        with _ignore_unfixable():
            yield from _filter_against_not(_cover_positive_for_type(ctx, schema, ty), schema, ctx)
    if GenerationMode.NEGATIVE in ctx.generation_modes:
        template = None
        if not ctx.can_be_negated(schema):
            return
        # `enum`/`const` without a sibling `type` (e.g. `canonicalish` strips `type` from
        # `{type: string, enum: [...]}` because the enum values already pin the type) would
        # otherwise miss type-violation negatives. Infer the type from the values once so the
        # `enum`/`const` branches below can dispatch `_negative_type` alongside `_negative_enum`.
        inferred_types: list[str] | None = None
        if "type" not in schema:
            if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
                inferred_types = sorted({to_json_type_name(v) for v in schema["enum"]})
            elif "const" in schema:
                inferred_types = [to_json_type_name(schema["const"])]
        # Snapshot: walking a keyword can push examples down into a schema shared with this one,
        # and a key landing here mid-walk is not one this pass was meant to cover anyway.
        for key, value in list(schema.items()):
            with _ignore_unfixable(), ctx.at(key):
                if _negation_ignored_by_dialect(ctx, key):
                    continue
                if key == "enum":
                    yield from _negative_enum(ctx, value, seen, schema)
                    if inferred_types:
                        yield from _negative_type(ctx, inferred_types, seen, schema)
                elif key == "const":
                    for value_ in _negative_enum(ctx, [value], seen, schema):
                        yield value_
                    if inferred_types:
                        yield from _negative_type(ctx, inferred_types, seen, schema)
                elif key == "type":
                    yield from _negative_type(ctx, value, seen, schema)
                elif key == "properties":
                    template = yield from _ensure_object_template_with_baseline(ctx, schema, template)
                    yield from _negative_properties(ctx, template, value)
                elif key == "patternProperties":
                    template = yield from _ensure_object_template_with_baseline(ctx, schema, template)
                    yield from _negative_pattern_properties(ctx, template, value)
                elif key == "propertyNames" and isinstance(value, dict):
                    template = yield from _ensure_object_template_with_baseline(ctx, schema, template)
                    if isinstance(template, dict):
                        yield from _negative_property_names(ctx, template, value)
                elif key == "items" and isinstance(value, dict):
                    parent_min_items = schema.get("minItems")
                    min_items = parent_min_items if isinstance(parent_min_items, int) else 0
                    yield from _negative_items(ctx, value, min_items=min_items)
                elif key == "items" and isinstance(value, list):
                    yield from _negative_prefix_items(ctx, value)
                elif key == "pattern":
                    min_length = schema.get("minLength")
                    max_length = schema.get("maxLength")
                    yield from _negative_pattern(ctx, value, min_length=min_length, max_length=max_length)
                elif key == "format" and ("string" in types or not types):
                    # Binary formats accept any bytes - no meaningful format violations
                    if value not in ("binary", "byte"):
                        yield from _negative_format(ctx, schema, value)
                elif key == "maximum":
                    # Legacy draft-4 `exclusiveMaximum: true` makes `maximum` itself the excluded boundary.
                    next = value if schema.get("exclusiveMaximum") is True else _just_past(schema, value, going_up=True)
                    if next is not None and seen.insert(next):
                        yield NegativeValue(
                            next,
                            scenario=CoverageScenario.VALUE_ABOVE_MAXIMUM,
                            description="Value greater than maximum",
                            location=ctx.current_path,
                        )
                elif key == "minimum":
                    # Legacy draft-4 `exclusiveMinimum: true` makes `minimum` itself the excluded boundary.
                    next = (
                        value if schema.get("exclusiveMinimum") is True else _just_past(schema, value, going_up=False)
                    )
                    if next is not None and seen.insert(next):
                        yield NegativeValue(
                            next,
                            scenario=CoverageScenario.VALUE_BELOW_MINIMUM,
                            description="Value smaller than minimum",
                            location=ctx.current_path,
                        )
                elif key == "exclusiveMaximum" or key == "exclusiveMinimum" and seen.insert(value):
                    if isinstance(value, bool):
                        continue
                    verb = "greater" if key == "exclusiveMaximum" else "smaller"
                    limit = "maximum" if key == "exclusiveMaximum" else "minimum"
                    scenario = (
                        CoverageScenario.VALUE_ABOVE_MAXIMUM
                        if key == "exclusiveMaximum"
                        else CoverageScenario.VALUE_BELOW_MINIMUM
                    )
                    yield NegativeValue(
                        value, scenario=scenario, description=f"Value {verb} than {limit}", location=ctx.current_path
                    )
                elif key == "multipleOf":
                    for value_ in _negative_multiple_of(ctx, schema, value):
                        if seen.insert(value_.value):
                            yield value_
                elif key == "minLength" and 0 < value < INTERNAL_BUFFER_SIZE:
                    # minLength only constrains strings; skip when schema explicitly excludes string type
                    if "string" in get_type(schema):
                        if value == 1:
                            # In this case, the only possible negative string is an empty one
                            # The `pattern` value may require an non-empty one and the generation will fail
                            # However, it is fine to violate `pattern` here as it is negative string generation anyway
                            value = ""
                            if ctx.is_valid_for_location(value) and seen.insert(value):
                                yield NegativeValue(
                                    value,
                                    scenario=CoverageScenario.STRING_BELOW_MIN_LENGTH,
                                    description="String smaller than minLength",
                                    location=ctx.current_path,
                                )
                        else:
                            with suppress(InvalidArgument):
                                min_length = max_length = value - 1
                                new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                                new_schema.pop("enum", None)
                                new_schema.pop("const", None)
                                new_schema["type"] = "string"
                                if "pattern" in new_schema and ctx.update_pattern is not None:
                                    new_schema["pattern"] = ctx.update_pattern(
                                        schema["pattern"], min_length, max_length
                                    )
                                try:
                                    value = ctx.generate_from_schema(new_schema)
                                except Unsatisfiable:
                                    # Format or pattern may forbid the truncated length (e.g. no valid email of length 5).
                                    fallback = {k: v for k, v in new_schema.items() if k != "format"}
                                    if "pattern" in fallback:
                                        del fallback["minLength"]
                                        del fallback["maxLength"]
                                        value = ctx.generate_from_schema(fallback)[:max_length]
                                    elif fallback != new_schema:
                                        value = ctx.generate_from_schema(fallback)
                                    else:
                                        raise
                                if ctx.is_valid_for_location(value) and seen.insert(value):
                                    yield NegativeValue(
                                        value,
                                        scenario=CoverageScenario.STRING_BELOW_MIN_LENGTH,
                                        description="String smaller than minLength",
                                        location=ctx.current_path,
                                    )
                elif (
                    key == "maxLength"
                    and isinstance(value, int)
                    and value < MAX_STRING_LENGTH
                    and "string" in get_type(schema)
                ):
                    try:
                        target_length = value + 1
                        new_value: str | None
                        if target_length >= INTERNAL_BUFFER_SIZE:
                            # Cheap synthesis: any character violates the bound; bypass Hypothesis
                            # to avoid blowing past its internal buffer for very large limits.
                            new_value = "a" * target_length
                        else:
                            min_length = max_length = target_length
                            new_schema = {**schema, "minLength": min_length, "maxLength": max_length}
                            new_schema.pop("enum", None)
                            new_schema.pop("const", None)
                            new_schema["type"] = "string"
                            new_value = _generate_oversized_string(ctx, schema, new_schema, target_length)
                        if new_value is not None and seen.insert(new_value):
                            yield NegativeValue(
                                new_value,
                                scenario=CoverageScenario.STRING_ABOVE_MAX_LENGTH,
                                description="String larger than maxLength",
                                location=ctx.current_path,
                            )
                    except (InvalidArgument, Unsatisfiable):
                        pass
                elif key == "uniqueItems" and value:
                    yield from _negative_unique_items(ctx, schema)
                elif key == "required":
                    template = template or _generate_template_with_deflation_fallback(
                        ctx, schema, _get_template_schema(schema, "object", ctx)
                    )
                    yield from _negative_required(ctx, template, value)
                elif key == "maxItems" and isinstance(value, int) and value < INTERNAL_BUFFER_SIZE:
                    if value > NEGATIVE_MODE_MAX_ITEMS:
                        # It could be extremely slow to generate large arrays
                        # Generate values up to the limit and reuse them to construct the final array
                        new_schema = {
                            **schema,
                            "minItems": NEGATIVE_MODE_MAX_ITEMS,
                            "maxItems": NEGATIVE_MODE_MAX_ITEMS,
                            "type": "array",
                        }
                        array_value: list = []
                        if "items" in schema and isinstance(schema["items"], dict):
                            # The schema may have another large array nested, therefore generate covering cases
                            # and use them to build an array for the current schema
                            negative = [
                                case.value
                                for case in islice(cover_schema_iter(ctx, schema["items"]), NEGATIVE_MODE_MAX_ITEMS)
                            ]
                            positive = [
                                case.value
                                for case in islice(
                                    cover_schema_iter(ctx.with_positive(), schema["items"]), NEGATIVE_MODE_MAX_ITEMS
                                )
                            ]
                            # Interleave positive & negative values. Empty if either list is empty —
                            # fall back to direct generation below so the yielded array is non-empty.
                            array_value = [value for pair in zip(positive, negative, strict=False) for value in pair][
                                :NEGATIVE_MODE_MAX_ITEMS
                            ]
                        if not array_value:
                            try:
                                array_value = ctx.generate_from_schema(new_schema)
                            except (InvalidArgument, Unsatisfiable):
                                continue

                        # Extend the array to be of length value + 1 by repeating its own elements
                        diff = value + 1 - len(array_value)
                        if diff > 0 and array_value:
                            array_value += (
                                array_value * (diff // len(array_value)) + array_value[: diff % len(array_value)]
                            )
                        if seen.insert(array_value):
                            yield NegativeValue(
                                array_value,
                                scenario=CoverageScenario.ARRAY_ABOVE_MAX_ITEMS,
                                description="Array with more items than allowed by maxItems",
                                location=ctx.current_path,
                            )
                    else:
                        # Force the array to have one more item than allowed
                        new_schema = {**schema, "minItems": value + 1, "maxItems": value + 1, "type": "array"}
                        oversized: list | None = None
                        try:
                            oversized = ctx.generate_from_schema(new_schema)
                        except (InvalidArgument, Unsatisfiable):
                            # `uniqueItems: true` over a finite items domain (e.g. enum) makes a
                            # length-(max+1) unique array unsatisfiable; drop uniqueness so the
                            # maxItems violation still ships, even if it also violates uniqueItems.
                            if new_schema.get("uniqueItems"):
                                relaxed = {k: v for k, v in new_schema.items() if k != "uniqueItems"}
                                with suppress(InvalidArgument, Unsatisfiable):
                                    oversized = ctx.generate_from_schema(relaxed)
                        if oversized is not None and ctx.is_valid_for_location(oversized) and seen.insert(oversized):
                            yield NegativeValue(
                                oversized,
                                scenario=CoverageScenario.ARRAY_ABOVE_MAX_ITEMS,
                                description="Array with more items than allowed by maxItems",
                                location=ctx.current_path,
                            )
                elif key == "minItems" and isinstance(value, int) and value > 0:
                    if value == 1:
                        # The 0-item case is structurally trivial. Skip the Hypothesis round-trip
                        # so unresolvable / unsatisfiable `items` schemas don't drop the negative.
                        if ctx.is_valid_for_location([]) and seen.insert([]):
                            yield NegativeValue(
                                [],
                                scenario=CoverageScenario.ARRAY_BELOW_MIN_ITEMS,
                                description="Array with fewer items than allowed by minItems",
                                location=ctx.current_path,
                            )
                    else:
                        try:
                            # Drop spec hints: they describe valid shapes, so `generate_from_schema`
                            # would short-circuit to the example (vacuously accepted when a sibling
                            # `$ref` blocks validator construction) and skip the bound we install.
                            new_schema = {
                                k: v for k, v in schema.items() if k not in ("example", "examples", "default")
                            }
                            new_schema.update({"minItems": value - 1, "maxItems": value - 1, "type": "array"})
                            array_value = ctx.generate_from_schema(new_schema)
                            if ctx.is_valid_for_location(array_value) and seen.insert(array_value):
                                yield NegativeValue(
                                    array_value,
                                    scenario=CoverageScenario.ARRAY_BELOW_MIN_ITEMS,
                                    description="Array with fewer items than allowed by minItems",
                                    location=ctx.current_path,
                                )
                        except (InvalidArgument, Unsatisfiable):
                            pass
                elif key == "additionalProperties" and schema.get("type") in ["object", None]:
                    if value is False and "pattern" not in schema:
                        # additionalProperties: false - add unexpected property
                        if not ctx.allow_extra_parameters and ctx.location in (
                            ParameterLocation.QUERY,
                            ParameterLocation.HEADER,
                            ParameterLocation.COOKIE,
                            ParameterLocation.BODY,
                        ):
                            continue
                        template = template or _generate_template_with_deflation_fallback(
                            ctx, schema, _get_template_schema(schema, "object", ctx)
                        )
                        unexpected_key = _unexpected_property_key(
                            schema, set(template) | set(schema.get("properties", {}))
                        )
                        if unexpected_key is None:
                            continue
                        yield NegativeValue(
                            {**template, unexpected_key: UNKNOWN_PROPERTY_VALUE},
                            scenario=CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES,
                            description="Object with unexpected properties",
                            location=ctx.current_path,
                        )
                    elif isinstance(value, dict):
                        # additionalProperties with schema - generate invalid values for the schema
                        template = template or _generate_template_with_deflation_fallback(
                            ctx, schema, _get_template_schema(schema, "object", ctx)
                        )
                        existing_keys = set(schema.get("properties", {}).keys()) | set(template.keys())
                        additional_key = _pick_property_name(schema, existing_keys, ctx)
                        if additional_key is None:
                            continue
                        nctx = ctx.with_negative()
                        with nctx.at(additional_key):
                            for invalid in cover_schema_iter(nctx, value):
                                yield NegativeValue(
                                    {**template, additional_key: invalid.value},
                                    scenario=invalid.scenario,
                                    description=f"Object with invalid additional property: {invalid.description}",
                                    location=nctx.current_path,
                                )
                elif key == "maxProperties" and isinstance(value, int) and 0 <= value < INTERNAL_BUFFER_SIZE:
                    additional_properties = schema.get("additionalProperties", True)
                    # Skip if additionalProperties is false - can't add more properties cleanly
                    if additional_properties is False:
                        continue
                    template = template or _generate_template_with_deflation_fallback(
                        ctx, schema, _get_template_schema(schema, "object", ctx)
                    )
                    obj_value = dict(template)
                    existing_keys = set(obj_value.keys())
                    needed = value + 1 - len(existing_keys)
                    if needed > 0:
                        for taken in range(needed):
                            # Earlier picks took every lower number, so scanning from here finds the same key faster.
                            new_key = _pick_property_name(schema, existing_keys, ctx, start=taken)
                            if new_key is None:
                                break
                            existing_keys.add(new_key)
                            # Generate value based on additionalProperties schema, or use a default
                            if isinstance(additional_properties, dict):
                                obj_value[new_key] = ctx.generate_from_schema(additional_properties)
                            else:
                                obj_value[new_key] = UNKNOWN_PROPERTY_VALUE
                    if len(obj_value) > value and seen.insert(obj_value):
                        yield NegativeValue(
                            obj_value,
                            scenario=CoverageScenario.OBJECT_ABOVE_MAX_PROPERTIES,
                            description="Object with more properties than allowed by maxProperties",
                            location=ctx.current_path,
                        )
                elif key == "minProperties" and isinstance(value, int) and value > 0:
                    try:
                        required = schema.get("required", [])
                        if value == 1 and not required:
                            # Only use empty object if no required properties
                            obj_value = {}
                        else:
                            new_schema = {
                                **schema,
                                "type": "object",
                                "minProperties": value - 1,
                                "maxProperties": value - 1,
                            }
                            obj_value = ctx.generate_from_schema(new_schema)
                        if seen.insert(obj_value):
                            yield NegativeValue(
                                obj_value,
                                scenario=CoverageScenario.OBJECT_BELOW_MIN_PROPERTIES,
                                description="Object with fewer properties than allowed by minProperties",
                                location=ctx.current_path,
                            )
                    except (InvalidArgument, Unsatisfiable):
                        pass
                elif key == "allOf":
                    nctx = ctx.with_negative()
                    if len(value) == 1:
                        with nctx.at(0):
                            yield from cover_schema_iter(nctx, value[0], seen)
                    else:
                        with _ignore_unfixable():
                            folded = _merge_all_of(schema)
                            # A branch that cannot be folded would loop if recursed on as a whole;
                            # iterate sub-schemas instead.
                            if folded is None:
                                for idx, sub in enumerate(value):
                                    with nctx.at(idx):
                                        yield from cover_schema_iter(nctx, sub, seen)
                            else:
                                yield from cover_schema_iter(nctx, folded, seen)
                elif key == "anyOf":
                    nctx = ctx.with_negative()
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in value
                    ]
                    validators = _make_branch_validators(resolved_schemas, ctx)
                    # Body fields in multipart/form-urlencoded are serialized as strings via str().
                    # Query/path/header parameters are also stringified, but servers parse them
                    # back to their declared type before validation, so str() doesn't make them
                    # valid for explicitly string-typed branches in that case.
                    stringify_body_fields = ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type)
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                # Negative value for this schema could be a positive value for another one
                                if is_valid_for_others(
                                    value.value, idx, validators, resolved_schemas, stringify_body_fields
                                ):
                                    continue
                                yield value
                elif key == "oneOf":
                    nctx = ctx.with_negative()
                    resolved_schemas = [
                        ctx.resolve_ref(s["$ref"]) if isinstance(s, dict) and "$ref" in s else s for s in value
                    ]
                    validators = _make_branch_validators(resolved_schemas, ctx)
                    for idx, sub_schema in enumerate(value):
                        with nctx.at(idx):
                            for value in cover_schema_iter(nctx, sub_schema, seen):
                                if is_invalid_for_oneOf(value.value, idx, validators):
                                    yield value
                elif key == "not" and isinstance(value, dict | bool):
                    # For 'not' schemas: generate positive cases of inner schema (valid values)
                    # These valid values are negative for the outer schema, so flip the mode
                    pctx = ctx.with_positive()
                    yield from _flip_generation_mode_for_not(cover_schema_iter(pctx, value, seen))


def is_valid_for_others(
    value: Any,
    idx: int,
    validators: list[jsonschema_rs.Validator],
    schemas: list[dict | bool] | None = None,
    will_be_serialized_to_string: bool = False,
) -> bool:
    if contains_binary(value):
        return False
    for vidx, validator in enumerate(validators):
        if idx == vidx:
            # This one is being negated
            continue
        if validator.is_valid(value):
            return True
        # In serialized contexts (multipart, form-urlencoded, path/query/header), non-string
        # values are converted via str() before transmission. Only skip if the other branch
        # explicitly requires string type — schemas without a type constraint accept strings
        # vacuously (e.g. `minimum` doesn't apply to strings), which would be a false match.
        if will_be_serialized_to_string and not isinstance(value, str) and schemas is not None:
            other = schemas[vidx]
            if isinstance(other, dict):
                explicit_type = other.get("type")
                has_string = explicit_type == "string" or (
                    isinstance(explicit_type, list) and "string" in explicit_type
                )
                if has_string and validator.is_valid(str(value)):
                    return True
    return False


def is_invalid_for_oneOf(value: object, idx: int, validators: list[jsonschema_rs.Validator]) -> bool:
    if contains_binary(value):
        # Binary values cannot be validated by jsonschema_rs; treat as not matching any other sub-schema
        return True
    valid_count = 0
    for vidx, validator in enumerate(validators):
        if idx == vidx:
            # This one is being negated
            continue
        if validator.is_valid(value):
            valid_count += 1
            # Should circuit - no need to validate more, it is already invalid
            if valid_count > 1:
                return True
    # No matching at all - we successfully generated invalid value
    return valid_count == 0


def _positive_for_describing_keywords(
    ctx: CoverageContext, schema: JsonSchemaObject, ty: str | None, template: object
) -> Generator[GeneratedValue, None, None]:
    """Positive values built from the describing keywords alone (type, bounds, `items`, `properties`, ...)."""
    if ty == "null":
        yield PositiveValue(None, scenario=CoverageScenario.NULL_VALUE, description="Value null value")
    elif ty == "boolean":
        yield PositiveValue(True, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value")
        yield PositiveValue(False, scenario=CoverageScenario.VALID_BOOLEAN, description="Valid boolean value")
    elif ty == "string":
        yield from _positive_string(ctx, schema)
    elif ty == "integer" or ty == "number":
        yield from _positive_number(ctx, schema)
    elif ty == "array":
        yield from _drop_invalid_for_location(_positive_array(ctx, schema, cast(list, template)), ctx)
    elif ty == "object":
        yield from _drop_invalid_for_location(
            _positive_object(ctx, _with_effective_required(schema), cast(dict, template)), ctx
        )
    elif ty is None:
        if _implies_object_type(schema):
            yield from _drop_invalid_for_location(
                _positive_object(ctx, _with_effective_required(schema), cast(dict, template)), ctx
            )
        elif _implies_array_type(schema):
            yield from _drop_invalid_for_location(_positive_array(ctx, schema, cast(list, template)), ctx)


def _drop_invalid_for_location(
    cases: Generator[GeneratedValue, None, None], ctx: CoverageContext
) -> Generator[GeneratedValue, None, None]:
    """Drop containers the location cannot carry, e.g. ones whose rendering blanks a path segment."""
    for case in cases:
        value = case.value
        if isinstance(value, dict):
            # `is_valid_for_location` judges a dict by its `repr`, which is not what a path
            # parameter sends; only an empty object is unrepresentable there.
            if ctx.location == ParameterLocation.PATH and not value:
                continue
        elif not ctx.is_valid_for_location(value):
            continue
        yield case


def _filter_against_combinators(
    cases: Generator[GeneratedValue, None, None], schema: JsonSchema, ctx: CoverageContext
) -> Generator[GeneratedValue, None, None]:
    """Drop values that violate `anyOf`/`oneOf` on the same schema.

    Values are built from the describing keywords alone, without consulting sibling `anyOf`/`oneOf`
    constraints (e.g. a branch tightening a property's enum or a string's length). When such a
    combinator is present, validate each generated value against the full schema and drop the ones
    no branch accepts.
    """
    if not isinstance(schema, dict) or ("anyOf" not in schema and "oneOf" not in schema):
        yield from cases
        return
    for case in cases:
        if _is_valid_with_formats(case.value, schema, ctx):
            yield case


def _filter_against_not(
    cases: Generator[GeneratedValue, None, None], schema: JsonSchema, ctx: CoverageContext
) -> Generator[GeneratedValue, None, None]:
    """Drop values that a sibling `not` rules out.

    Values are built from the describing keywords alone (bounds, `items`, `properties`, ...), so a
    `not` beside them can still reject what those keywords allow.
    """
    if not isinstance(schema, dict) or not isinstance(schema.get("not"), (dict, bool)):
        yield from cases
        return
    for case in cases:
        if _is_valid_with_formats(case.value, schema, ctx):
            yield case


def _is_valid_with_formats(value: object, schema: JsonSchema, ctx: CoverageContext) -> bool:
    """Whether the schema admits the value, passing a value nothing can check."""
    if not isinstance(schema, dict):
        return True
    return _admitted(value, schema, ctx, unjudged=True)


def _make_branch_validators(schemas: list[JsonSchema], ctx: CoverageContext) -> list[jsonschema_rs.Validator]:
    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY)
    result = []
    for schema in schemas:
        if bundle is not None and isinstance(schema, dict):
            schema = {**schema, BUNDLE_STORAGE_KEY: bundle}
        result.append(make_validator_for(schema))
    return result


def _get_properties(schema: JsonSchema, ctx: CoverageContext) -> JsonSchema:
    if isinstance(schema, dict):
        # A single-valued `enum` pins the same value as `const` and, unlike it, is a keyword in
        # every draft - Draft 4 drops `const`, leaving the property free to draw anything.
        if "example" in schema:
            example = schema["example"]
            if _is_valid_with_formats(example, schema, ctx):
                return {"enum": [example]}
        if "default" in schema:
            default = schema["default"]
            if _is_valid_with_formats(default, schema, ctx):
                return {"enum": [default]}
        if schema.get("examples"):
            valid = [ex for ex in schema["examples"] if _is_valid_with_formats(ex, schema, ctx)]
            if valid:
                return {"enum": valid}
        if schema.get("type") == "object":
            return _get_template_schema(schema, "object", ctx)
        # Without forcing object generation here, Hypothesis treats `properties`-only or
        # `$ref`-to-properties-only sub-schemas as "any value" and can emit `null` or `{}`.
        implied: JsonSchemaObject | None = None
        if "$ref" in schema and not ctx.is_exhausted(schema["$ref"]):
            try:
                candidate = ctx.resolve_ref(schema["$ref"])
                if isinstance(candidate, dict) and (
                    candidate.get("type") == "object" or ("type" not in candidate and _implies_object_type(candidate))
                ):
                    implied = candidate
            except RefResolutionError:
                pass
        elif "type" not in schema and _implies_object_type(schema):
            implied = schema
        if implied is not None:
            # Without inflating `required`, the template is `{}` for schemas that declare
            # properties but no required list. Keep original required so keys outside
            # `properties` still appear.
            properties = implied.get("properties") or {}
            original_required = list(implied.get("required") or [])
            inflated_required = list(
                dict.fromkeys(original_required + [k for k, v in properties.items() if v != {"not": {}}])
            )
            reference = schema.get("$ref")
            # Whatever the template pulls in through this pointer counts against its budget, so a
            # cyclic one stops instead of nesting forever.
            with ctx.expand(reference) if isinstance(reference, str) else nullcontext():
                return _get_template_schema({**implied, "required": inflated_required}, "object", ctx)
        _schema = deepclone(schema)
        if ctx.update_pattern is not None:
            _update_schema_pattern(_schema, ctx.update_pattern)
        # Drop hints their own `format` rejects, so none of them seeds a value the schema rules out.
        if "default" in _schema and not _is_valid_with_formats(_schema["default"], _schema, ctx):
            del _schema["default"]
        if "example" in _schema and not _is_valid_with_formats(_schema["example"], _schema, ctx):
            del _schema["example"]
        if "examples" in _schema:
            valid_examples = [ex for ex in _schema["examples"] if _is_valid_with_formats(ex, _schema, ctx)]
            if valid_examples:
                _schema["examples"] = valid_examples
            else:
                del _schema["examples"]
        if _schema.get("type") == "string" and _xml_string_needs_non_empty(ctx, _schema):
            _schema["minLength"] = 1
        return _schema
    return schema


def _xml_string_needs_non_empty(ctx: CoverageContext, schema: JsonSchemaObject) -> bool:
    # Empty XML elements (<tag></tag>) round-trip as None on common parsers (etree, xmltodict,
    # default Jackson), so positive cases never exercise server-side string keywords and "kept-valid"
    # context in negative cases reaches the server malformed. Force >= 1 character.
    if ctx.location != ParameterLocation.BODY or ctx.media_type is None or not is_xml_parts(ctx.media_type):
        return False
    if schema.get("minLength") not in (None, 0):
        return False
    max_length = schema.get("maxLength")
    if max_length is not None and max_length < 1:
        return False
    return "enum" not in schema and "const" not in schema


_FAST_PATH_KEYS = frozenset({"properties", "required", "type"})


_OBJECT_ONLY_KEYWORDS = ("properties", "required", "patternProperties", "propertyNames", "dependencies")

_ARRAY_ONLY_KEYWORDS = (
    "items",
    "prefixItems",
    "additionalItems",
    "unevaluatedItems",
    "minItems",
    "maxItems",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
)


def _implies_object_type(schema: JsonSchemaObject) -> bool:
    # `additionalProperties: {schema}` implicitly types the value as an object even when
    # `type: object` is omitted (common in Azure swagger 2.0 tag maps); without this the
    # positive object generator never runs and the keyword stays uncovered.
    if any(key in schema for key in _OBJECT_ONLY_KEYWORDS):
        return True
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        return True
    return False


def _implies_array_type(schema: JsonSchemaObject) -> bool:
    # Swagger 2.0 / OpenAPI schemas commonly omit `type: array` on properties carrying only
    # `items` (e.g. clearblade.com). Without an array-typed positive variant the items
    # sub-schema is never exercised positively and any `$ref`-pulled definition stays uncovered.
    return any(key in schema for key in _ARRAY_ONLY_KEYWORDS)


def _type_excludes_object(schema: JsonSchemaObject) -> bool:
    ty = schema.get("type")
    if isinstance(ty, str):
        return ty != "object"
    if isinstance(ty, list):
        return "object" not in ty
    return False


def _ensure_object_template_with_baseline(
    ctx: CoverageContext, schema: JsonSchemaObject, template: Any
) -> Generator[GeneratedValue, None, Any]:
    # First-time object template build emits a baseline `NegativeValue` when the outer type
    # excludes object; the inner `properties` applicator otherwise never sees an
    # all-children-valid case (per-leaf negatives each break one child).
    if template is not None:
        return template
    template = _generate_template_with_deflation_fallback(ctx, schema, _get_template_schema(schema, "object", ctx))
    if isinstance(template, dict) and _type_excludes_object(schema):
        yield NegativeValue(
            template,
            scenario=CoverageScenario.INCORRECT_TYPE,
            description="Object body where non-object type expected",
            location=ctx.current_path,
        )
    return template


def _get_template_schema(schema: JsonSchemaObject, ty: str, ctx: CoverageContext) -> JsonSchemaObject:
    if ty == "object":
        properties = schema.get("properties")
        if properties is not None:
            required = schema.get("required", [])
            additional = schema.get("additionalProperties", True)
            if additional is not False:
                # Declaring a name under `properties` exempts it from `additionalProperties`, so a required name
                # matching no `patternProperties` entry carries that schema as its placeholder instead.
                patterns = _pattern_property_regexes(schema)
                extra: dict[str, JsonSchemaObject] = {
                    k: {} if additional is True or any(pattern.search(k) for pattern in patterns) else additional
                    for k in required
                    if k not in properties
                }
            else:
                extra = {}
            all_properties = {
                **{k: _get_properties(v, ctx) for k, v in properties.items()},
                **extra,
            }
            # When the fast path fires, required is used to decide what's truly required;
            # keep it at the schema's original required to avoid aborting on optional
            # properties with unsatisfiable schemas.  Otherwise inflate to all_properties
            # so every defined property appears in the generated template.
            # Ignore non-structural keys (annotations like `title`, OpenAPI `nullable`,
            # `readOnly`, `x-*` extensions); only JSON Schema keywords gate the choice.
            schema_keys = {k for k in schema if k in ALL_KEYWORDS}
            # `{"not": {}}` marks a property as forbidden; requiring it makes the template unsatisfiable.
            forbidden = {k for k, v in all_properties.items() if v == {"not": {}}}
            # Inflating may add to `required`, never take from it: a name the schema requires but does not
            # declare stays required, so an object nothing can satisfy is generated as such.
            kept = [k for k in required if k not in forbidden]
            if schema_keys <= _FAST_PATH_KEYS:
                required_for_template = kept
            else:
                required_for_template = list(dict.fromkeys([*kept, *(k for k in all_properties if k not in forbidden)]))
            return {
                **schema,
                "required": required_for_template,
                "type": ty,
                "properties": all_properties,
            }
    return {**schema, "type": ty}


def _get_not_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    """Safely get the 'not' schema as a dict, handling boolean schemas."""
    not_schema = schema.get("not", {})
    if isinstance(not_schema, dict):
        return not_schema.copy()
    return {}


def _ensure_valid_path_parameter_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    # Path parameters should have at least 1 character length and don't contain any characters with special treatment
    # on the transport level.
    # The implementation below sneaks into `not` to avoid clashing with existing `pattern` keyword
    not_ = _get_not_schema(schema)
    not_["pattern"] = r"[/{}]"
    min_length = max(schema.get("minLength", 0), 1)
    return {**schema, "minLength": min_length, "not": not_}


# Characters `_ensure_valid_headers_schema` keeps; a pattern requiring anything else is unsatisfiable for headers.
HEADER_ALLOWED_CHARS = string.ascii_letters + string.digits


def _ensure_valid_headers_schema(schema: JsonSchemaObject) -> JsonSchemaObject:
    # Reject any character that is not A-Z, a-z, or 0-9 for simplicity
    not_ = _get_not_schema(schema)
    not_["pattern"] = r"[^A-Za-z0-9]"
    return {**schema, "not": not_}


def _positive_string(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive string values."""
    # Pin type to "string"; for unions like ["string","null"] the dispatcher yields null separately,
    # without this override generation here may pick null and drop the boundary-length variants.
    schema = {**schema, "type": "string"}
    min_length = schema.get("minLength")
    if min_length == 0:
        min_length = None
    max_length = schema.get("maxLength")
    if max_length is not None and (min_length or 0) > max_length:
        return
    # Spec hints are checked against `declared`: the header/cookie narrowing below only simplifies
    # generated values, and values the transport cannot send are rejected separately.
    declared = schema
    if ctx.location == "path" and not ("format" in schema and schema["format"] in ctx.custom_formats):
        schema = _ensure_valid_path_parameter_schema(schema)
        declared = schema
    elif ctx.location in ("header", "cookie") and not ("format" in schema and schema["format"] in ctx.custom_formats):
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and pattern_requires_char_outside(pattern, HEADER_ALLOWED_CHARS):
            return
        # Don't apply it for known formats - they will insure the correct format during generation
        schema = _ensure_valid_headers_schema(schema)
    elif _xml_string_needs_non_empty(ctx, schema):
        schema = {**schema, "minLength": 1}
        declared = schema
        min_length = 1

    # Sentinel-based reads so falsy spec hints (`default: 0`, `example: ""`) and explicit
    # `default: null` / `example: null` aren't confused with "key absent".
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    # Two-layer check to avoid potentially expensive data generation using schema constraints as a key
    seen_values = HashSet()
    seen_constraints: set[tuple] = set()

    if example is not NOT_SET or examples or default is not NOT_SET:
        has_valid_example = False
        if (
            example is not NOT_SET
            and _is_valid_with_formats(example, declared, ctx)
            and ctx.is_valid_for_location(example)
            and seen_values.insert(example)
        ):
            has_valid_example = True
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if (
                    _is_valid_with_formats(example, declared, ctx)
                    and ctx.is_valid_for_location(example)
                    and seen_values.insert(example)
                ):
                    has_valid_example = True
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, declared, ctx)
            and ctx.is_valid_for_location(default)
            and seen_values.insert(default)
        ):
            has_valid_example = True
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
        if not has_valid_example:
            if not min_length and not max_length or "pattern" in schema:
                value = ctx.generate_from_schema(schema)
                seen_values.insert(value)
                seen_constraints.add((min_length, max_length))
                yield PositiveValue(value, scenario=CoverageScenario.VALID_STRING, description="Valid string")
    elif not min_length and not max_length or "pattern" in schema:
        value = ctx.generate_from_schema(schema)
        seen_values.insert(value)
        seen_constraints.add((min_length, max_length))
        yield PositiveValue(value, scenario=CoverageScenario.VALID_STRING, description="Valid string")

    if min_length is not None and min_length < INTERNAL_BUFFER_SIZE:
        # Exactly the minimum length
        key = (min_length, min_length)
        if key not in seen_constraints:
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "maxLength": min_length})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value, scenario=CoverageScenario.MINIMUM_LENGTH_STRING, description="Minimum length string"
                    )

        # One character more than minimum if possible
        larger = min_length + 1
        key = (larger, larger)
        if larger < INTERNAL_BUFFER_SIZE and key not in seen_constraints and (not max_length or larger <= max_length):
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": larger, "maxLength": larger})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.NEAR_BOUNDARY_LENGTH_STRING,
                        description="Near-boundary length string",
                    )

    if max_length is not None:
        # Exactly the maximum length
        key = (max_length, max_length)
        if max_length < INTERNAL_BUFFER_SIZE and key not in seen_constraints:
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": max_length, "maxLength": max_length})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value, scenario=CoverageScenario.MAXIMUM_LENGTH_STRING, description="Maximum length string"
                    )

        # One character less than maximum if possible
        smaller = max_length - 1
        key = (smaller, smaller)
        if (
            smaller < INTERNAL_BUFFER_SIZE
            and key not in seen_constraints
            and (smaller > 0 and (min_length is None or smaller >= min_length))
        ):
            seen_constraints.add(key)
            with _ignore_unfixable():
                value = ctx.generate_from_schema({**schema, "minLength": smaller, "maxLength": smaller})
                if seen_values.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.NEAR_BOUNDARY_LENGTH_STRING,
                        description="Near-boundary length string",
                    )

    if not seen_values:
        # Length bounds past the internal generation buffer leave every boundary case above
        # unreachable; without a plain value the parameter is absent from every generated case.
        with _ignore_unfixable():
            value = ctx.generate_from_schema(schema)
            yield PositiveValue(value, scenario=CoverageScenario.VALID_STRING, description="Valid string")


def _as_number(value: Decimal) -> int | float:
    """A decimal as JSON spells it: an integer where it is whole, since past 2**53 a float may not be able to."""
    return int(value) if value == value.to_integral_value() else float(value)


def closest_multiple_greater_than(y: int | float, x: int | float) -> int | float:
    """Find the closest multiple of X that is greater than Y."""
    if isinstance(y, int) and isinstance(x, int):
        return y if y % x == 0 else y + x - y % x
    # Decimal arithmetic via the canonical `repr` keeps the value exact where IEEE-754 drifts.
    step = Decimal(str(x))
    quotient, remainder = divmod(Decimal(str(y)), step)
    return _as_number(Decimal(str(y)) if remainder == 0 else step * (quotient + 1))


def _shift_by_multiple(value: int | float, step: int | float, *, direction: int) -> int | float:
    if isinstance(value, int) and isinstance(step, int):
        return value + direction * step
    return _as_number(Decimal(str(value)) + direction * Decimal(str(step)))


def _largest_multiple_within(value: int | float, step: int | float) -> int | float:
    if isinstance(value, int) and isinstance(step, int):
        return value - (value % step)
    decimal_step = Decimal(str(step))
    return _as_number(Decimal(str(value)) - (Decimal(str(value)) % decimal_step))


def _exact(bound: int | float) -> int | Decimal:
    """A float bound as the decimal its JSON text spells, which is what the validator compares against."""
    return bound if isinstance(bound, int) else Decimal(str(bound))


def _adjust_numeric_bound(
    value: int | float, *, is_integer: bool, going_up: bool, is_float32: bool = False
) -> int | float:
    if is_integer:
        # Stepped in integer arithmetic: a unit step vanishes on a float past 2**53.
        return floor(_exact(value)) + 1 if going_up else ceil(_exact(value)) - 1
    if is_float32:
        return next_float32(value, going_up=going_up)
    return nextafter(float(value), inf if going_up else -inf)


def _just_past(schema: JsonSchemaObject, bound: int | float, *, going_up: bool) -> int | float | None:
    """The value one step outside an inclusive bound: a unit where a unit exists, the float spacing past it.

    `None` where no representable value lies past the bound.
    """
    direction = 1 if going_up else -1
    declared = schema.get("type")
    if "integer" in (declared if isinstance(declared, list) else [declared]):
        return (floor(_exact(bound)) if going_up else ceil(_exact(bound))) + direction
    if schema.get("format") == "float":
        # A single-precision reader narrows the value, so the step reaches at least the next single.
        edge = next_float32(bound, going_up=going_up)
        if edge in (inf, -inf):
            return None
        if abs(edge - bound) > 1:
            return bound + direction * abs(edge - bound)
    if isinstance(bound, int):
        return bound + direction
    stepped = bound + direction * max(1.0, ulp(bound))
    return None if stepped in (inf, -inf) else stepped


def _positive_number(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    """Generate positive integer values."""
    # Pin type to "integer" or "number"; for unions like ["string","number","null"] the
    # dispatcher yields the other branches separately, and without this override generation
    # here would draw from the union and miss the numeric variant entirely.
    declared = schema.get("type")
    declared_types = declared if isinstance(declared, list) else [declared]
    pinned = "integer" if "integer" in declared_types else "number"
    schema = {**schema, "type": pinned}
    is_integer = pinned == "integer"
    is_float32 = not is_integer and schema.get("format") == "float"
    if is_integer:
        # Draft 4 reads `1.0` as a number, so an integer's boundary is the nearest integer inside the bound; rounded
        # first, in decimal, so it compares with a stepped exclusive bound the way the validator reads both.
        rounded = {
            key: int(rounding(_exact(schema[key])))
            for key, rounding in (("minimum", ceil), ("maximum", floor))
            if is_numeric_bound(schema.get(key))
        }
        schema = {**schema, **rounded}
    minimum, maximum = resolve_inclusive_bounds(
        schema,
        step=lambda value, going_up: _adjust_numeric_bound(
            value, is_integer=is_integer, going_up=going_up, is_float32=is_float32
        ),
    )
    if bounds_are_unsatisfiable(minimum, maximum):
        # Nothing representable past the bound, so emit no value.
        return
    multiple_of = schema.get("multipleOf")
    if is_integer and multiple_of is not None and not isinstance(multiple_of, int):
        # Integer multiples of `p/q` are exactly the multiples of `p`.
        multiple_of = Fraction(Decimal(str(multiple_of))).numerator
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    seen = HashSet()

    def _within_adjusted_bounds(value: int | float) -> bool:
        return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)

    if example is not NOT_SET or examples or default is not NOT_SET:
        has_valid_example = False
        if (
            example is not NOT_SET
            and _is_valid_with_formats(example, schema, ctx)
            and _within_adjusted_bounds(example)
            and seen.insert(example)
        ):
            has_valid_example = True
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if (
                    _is_valid_with_formats(example, schema, ctx)
                    and _within_adjusted_bounds(example)
                    and seen.insert(example)
                ):
                    has_valid_example = True
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
            and _within_adjusted_bounds(default)
            and seen.insert(default)
        ):
            has_valid_example = True
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
        if not has_valid_example and minimum is None and maximum is None:
            value = ctx.generate_from_schema(schema)
            if seen.insert(value):
                yield PositiveValue(value, scenario=CoverageScenario.VALID_NUMBER, description="Valid number")
    elif minimum is None and maximum is None:
        value = ctx.generate_from_schema(schema)
        seen.insert(value)
        yield PositiveValue(value, scenario=CoverageScenario.VALID_NUMBER, description="Valid number")

    if minimum is not None:
        # Exactly the minimum
        if multiple_of is not None:
            smallest = closest_multiple_greater_than(minimum, multiple_of)
        else:
            smallest = minimum
        if _within_adjusted_bounds(smallest) and seen.insert(smallest):
            yield PositiveValue(smallest, scenario=CoverageScenario.MINIMUM_VALUE, description="Minimum value")

        # One more than minimum if possible
        if multiple_of is not None:
            larger = _shift_by_multiple(smallest, multiple_of, direction=1)
        else:
            larger = minimum + 1
        if (maximum is None or larger <= maximum) and seen.insert(larger):
            yield PositiveValue(
                larger, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )

    if maximum is not None:
        # Exactly the maximum
        if multiple_of is not None:
            largest = _largest_multiple_within(maximum, multiple_of)
        else:
            largest = maximum
        if _within_adjusted_bounds(largest) and seen.insert(largest):
            yield PositiveValue(largest, scenario=CoverageScenario.MAXIMUM_VALUE, description="Maximum value")

        # One less than maximum if possible
        if multiple_of is not None:
            smaller = _shift_by_multiple(largest, multiple_of, direction=-1)
        else:
            smaller = maximum - 1
        if (minimum is None or smaller >= minimum) and seen.insert(smaller):
            yield PositiveValue(
                smaller, scenario=CoverageScenario.NEAR_BOUNDARY_NUMBER, description="Near-boundary number"
            )


def _positive_array(
    ctx: CoverageContext, schema: JsonSchemaObject, template: list
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    seen = HashSet()
    seen_constraints: set[tuple] = set()

    if example is not NOT_SET or examples or default is not NOT_SET:
        if example is not NOT_SET and _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
            yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                if _is_valid_with_formats(example, schema, ctx) and seen.insert(example):
                    yield PositiveValue(example, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
            and _is_valid_with_formats(default, schema, ctx)
            and seen.insert(default)
        ):
            yield PositiveValue(default, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    else:
        # An empty template skips every items-level keyword on the wire; surface a non-empty
        # baseline first so the recorder sees items satisfied. Skip when `maxItems` forbids any.
        items = schema.get("items")
        if not template and isinstance(items, dict) and items and schema.get("maxItems") != 0:
            for item in cover_schema_iter(ctx, items):
                candidate = [item.value]
                if seen.insert(candidate):
                    yield PositiveValue(candidate, scenario=CoverageScenario.VALID_ARRAY, description="Valid array")
                    break
        if seen.insert(template):
            yield PositiveValue(template, scenario=CoverageScenario.VALID_ARRAY, description="Valid array")

    # Boundary and near-boundary sizes
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    # `minContains` matching items must fit, so the array can never be shorter than it.
    if "contains" in schema:
        min_contains = schema.get("minContains", 1)
        if min_contains > 1:
            min_items = max(min_items or 0, min_contains)
    if min_items is not None:
        # Do not generate an array with `minItems` length, because it is already covered by `template`
        # One item more than minimum if possible
        larger = min_items + 1
        if (
            larger <= MAX_GENERATED_ITEMS
            and (max_items is None or larger <= max_items)
            and larger not in seen_constraints
        ):
            seen_constraints.add(larger)
            value = ctx.generate_from_schema({**schema, "minItems": larger, "maxItems": larger})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.NEAR_BOUNDARY_ITEMS_ARRAY, description="Near-boundary items array"
                )

    if max_items is not None:
        if max_items <= MAX_GENERATED_ITEMS and max_items not in seen_constraints:
            seen_constraints.add(max_items)
            value = ctx.generate_from_schema({**schema, "minItems": max_items})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.MAXIMUM_ITEMS_ARRAY, description="Maximum items array"
                )

        # One item smaller than maximum if possible
        smaller = max_items - 1
        if (
            MAX_GENERATED_ITEMS >= smaller > 0
            and (min_items is None or smaller >= min_items)
            and smaller not in seen_constraints
        ):
            value = ctx.generate_from_schema({**schema, "minItems": smaller, "maxItems": smaller})
            if seen.insert(value):
                yield PositiveValue(
                    value, scenario=CoverageScenario.NEAR_BOUNDARY_ITEMS_ARRAY, description="Near-boundary items array"
                )

    if (
        "items" in schema
        and isinstance(schema["items"], dict)
        and "enum" in schema["items"]
        and isinstance(schema["items"]["enum"], list)
        and max_items != 0
        # These synthesized arrays ignore `contains`; the repaired template covers those schemas.
        and "contains" not in schema
    ):
        # Ensure there is enough items to pass `minItems` if it is specified
        length = min_items or 1
        item_schema = schema["items"]
        enum_values = [v for v in item_schema["enum"] if _is_valid_with_formats(v, item_schema, ctx)]
        if schema.get("uniqueItems") and length > 1:
            for i, variant in enumerate(enum_values):
                others = [enum_values[j] for j in range(len(enum_values)) if j != i]
                value = [variant] + others[: length - 1]
                if seen.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.ENUM_VALUE_ITEMS_ARRAY,
                        description="Enum value from available for items array",
                    )
        else:
            for variant in enum_values:
                value = [variant] * length
                if seen.insert(value):
                    yield PositiveValue(
                        value,
                        scenario=CoverageScenario.ENUM_VALUE_ITEMS_ARRAY,
                        description="Enum value from available for items array",
                    )
    elif (
        "items" in schema
        and isinstance(schema["items"], dict)
        and (min_items is None or min_items <= 1)
        and (max_items is None or max_items >= 1)
        and "contains" not in schema
    ):
        # Single-item arrays exercise each items-schema branch individually.
        # `maxItems`-sized boundary arrays (above) repeat one shape and miss multi-branch coverage.
        sub_schema = schema["items"]
        for item in cover_schema_iter(ctx, sub_schema):
            candidate = [item.value]
            if seen.insert(candidate):
                yield PositiveValue(
                    candidate,
                    scenario=CoverageScenario.VALID_ARRAY,
                    description=f"Single-item array: {item.description}",
                )


def _positive_object(
    ctx: CoverageContext, schema: JsonSchemaObject, template: dict
) -> Generator[GeneratedValue, None, None]:
    # Synthesized property combinations ignore `dependentRequired`/`dependencies`/`dependentSchemas`;
    # drop any candidate the full schema rejects.
    enforce_dependencies = any(key in schema for key in ("dependentRequired", "dependencies", "dependentSchemas"))
    for generated in _iter_positive_object(ctx, schema, template):
        if not enforce_dependencies or is_valid(generated.value, schema):
            yield generated


def _accept_object_hint(value: Any, schema: JsonSchemaObject, ctx: CoverageContext) -> Any:
    if _is_valid_with_formats(value, schema, ctx):
        return value
    cleaned = _without_forbidden_keys(value, schema)
    if cleaned is not NOT_SET and _is_valid_with_formats(cleaned, schema, ctx):
        return cleaned
    return NOT_SET


def _iter_positive_object(
    ctx: CoverageContext, schema: JsonSchemaObject, template: dict
) -> Generator[GeneratedValue, None, None]:
    example = schema.get("example", NOT_SET)
    examples = schema.get("examples")
    default = schema.get("default", NOT_SET)

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    optional = sorted(set(properties) - required, key=str)
    min_props = schema.get("minProperties")

    # A required property absent from the template makes every derived combination schema-invalid.
    template_complete = not (required - set(template))
    # Whole-object dedup. Empty/partial templates make several scenarios
    # (Valid object, subset-of-optional, only-required) collapse to the same value.
    outer_seen = HashSet()

    if example is not NOT_SET or examples or default is not NOT_SET:
        if example is not NOT_SET:
            accepted = _accept_object_hint(example, schema, ctx)
            if accepted is not NOT_SET:
                yield PositiveValue(accepted, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if examples:
            for example in examples:
                accepted = _accept_object_hint(example, schema, ctx)
                if accepted is not NOT_SET:
                    yield PositiveValue(accepted, scenario=CoverageScenario.EXAMPLE_VALUE, description="Example value")
        if (
            default is not NOT_SET
            and not (example is not NOT_SET and default == example)
            and not (examples is not None and any(default == ex for ex in examples))
        ):
            accepted = _accept_object_hint(default, schema, ctx)
            if accepted is not NOT_SET:
                yield PositiveValue(accepted, scenario=CoverageScenario.DEFAULT_VALUE, description="Default value")
    elif template_complete and (template or not (ctx.is_required and is_form_parts(ctx.media_type))):
        outer_seen.insert(template)
        yield PositiveValue(template, scenario=CoverageScenario.VALID_OBJECT, description="Valid object")

    if not template_complete:
        return

    # Generate combinations with required properties and one optional property
    for name in optional:
        combo = {k: v for k, v in template.items() if k in required or k == name}
        if combo != template and (min_props is None or len(combo) >= min_props) and outer_seen.insert(combo):
            yield PositiveValue(
                combo,
                scenario=CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                description=f"Object with all required properties and '{name}'",
            )
    # Generate one combination for each size from 2 to N-1
    for selection in select_combinations(optional):
        combo = {k: v for k, v in template.items() if k in required or k in selection}
        if (min_props is None or len(combo) >= min_props) and outer_seen.insert(combo):
            yield PositiveValue(
                combo,
                scenario=CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                description="Object with all required and a subset of optional properties",
            )
    # Generate only required properties
    if set(properties) != required:
        only_required = {k: v for k, v in template.items() if k in required}
        # Skip empty object for required form bodies - {} serializes to no content
        # which violates requestBody.required
        if (
            (min_props is None or len(only_required) >= min_props)
            and (only_required or not (ctx.is_required and is_form_parts(ctx.media_type)))
            and outer_seen.insert(only_required)
        ):
            yield PositiveValue(
                only_required,
                scenario=CoverageScenario.OBJECT_ONLY_REQUIRED,
                description="Object with only required properties",
            )
    seen = HashSet()
    max_properties = schema.get("maxProperties")
    for name, sub_schema in properties.items():
        # A property the template left out adds a key, which the size window may not have room for.
        if name not in template and isinstance(max_properties, int) and len(template) + 1 > max_properties:
            continue
        # Skip pre-seed when the property is absent: `template.get(name)` would be None
        # and dedup legitimate null emissions for nullable optionals.
        if name in template:
            seen.insert(template[name])
        for new in cover_schema_iter(ctx, sub_schema):
            if seen.insert(new.value):
                yield PositiveValue(
                    {**template, name: new.value},
                    scenario=CoverageScenario.VALID_OBJECT,
                    description=f"Object with valid '{name}' value: {new.description}",
                )
        seen.clear()
    # Handle additionalProperties with schema
    additional_properties = schema.get("additionalProperties")
    if isinstance(additional_properties, dict):
        if isinstance(max_properties, int) and len(template) + 1 > max_properties:
            return
        existing_keys = set(properties.keys()) | set(template.keys())
        additional_key = _pick_property_name(schema, existing_keys, ctx)
        if additional_key is None:
            return
        for new in cover_schema_iter(ctx, additional_properties):
            if seen.insert(new.value):
                yield PositiveValue(
                    {**template, additional_key: new.value},
                    scenario=CoverageScenario.OBJECT_ADDITIONAL_PROPERTY,
                    description=f"Object with additional property: {new.description}",
                )


def select_combinations(optional: list[str]) -> Iterator[tuple[str, ...]]:
    for size in range(2, len(optional)):
        yield next(combinations(optional, size))


def _negative_enum(
    ctx: CoverageContext, value: list, seen: HashSet, schema: JsonSchemaObject | None = None
) -> Generator[GeneratedValue, None, None]:
    def is_not_in_value(x: Any) -> bool:
        if x in value or not ctx.is_valid_for_location(x):
            return False
        return seen.insert(x)

    strategy = (NEGATIVE_STRING_STRATEGY | st.none() | st.booleans() | NUMERIC_STRATEGY).filter(is_not_in_value)
    yield NegativeValue(
        ctx.generate_from(strategy),
        scenario=CoverageScenario.INVALID_ENUM_VALUE,
        description="Invalid enum value",
        location=ctx.current_path,
    )
    # Self-contradictory schemas (e.g. `enum: [2, 4]` or `const: 2` with `type: string`) skip every entry
    # on the positive path, so emit each mismatched entry as a negative to keep the keyword covered.
    if isinstance(schema, dict):
        declared_types = set(get_type(schema))
        if declared_types:
            for entry in value:
                entry_type = to_json_type_name(entry)
                if entry_type in declared_types:
                    continue
                # Integer values satisfy `type: number` in JSON Schema.
                if entry_type == "integer" and "number" in declared_types:
                    continue
                if not ctx.is_valid_for_location(entry) or not seen.insert(entry):
                    continue
                yield NegativeValue(
                    entry,
                    scenario=CoverageScenario.INCORRECT_TYPE,
                    description="Enum value with type mismatching the declared 'type'",
                    location=ctx.current_path,
                )


def _negative_properties(
    ctx: CoverageContext, template: dict, properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    is_form = ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type)
    is_xml = ctx.location == ParameterLocation.BODY and ctx.media_type is not None and is_xml_parts(ctx.media_type)
    bundle = ctx.root_schema.get(BUNDLE_STORAGE_KEY) if isinstance(ctx.root_schema, dict) else None
    for key, sub_schema in properties.items():
        validator: jsonschema_rs.Validator | None = None
        # Draft 4 ignores siblings of `$ref`, so generation against `{$ref, sibling}` may yield
        # values the body validator silently accepts; filter those out below.
        sub_has_ref = isinstance(sub_schema, dict) and "$ref" in sub_schema
        if isinstance(sub_schema, dict):
            # Cache by (sub_schema, bundle) identity — same pair recurs across operations.
            def _builder(s: dict = sub_schema, b: dict | None = bundle) -> JsonSchema:
                return s if b is None else {**s, BUNDLE_STORAGE_KEY: b}

            keep_alive: tuple[Any, ...] = (sub_schema,) if bundle is None else (sub_schema, bundle)
            try:
                validator = make_validator_with_seed(
                    schema_builder=_builder,
                    validator_cls=ctx.validator_cls,
                    seed=(id(sub_schema), id(bundle)),
                    keep_alive=keep_alive,
                )
            except Exception:
                pass
        with nctx.at(key):
            for value in cover_schema_iter(nctx, sub_schema):
                if validator is not None:
                    v = value.value
                    # Form bodies (urlencoded and multipart) stringify scalar property values
                    # on the wire; any non-string whose `str(v)` satisfies the property schema
                    # is a no-op mutation.
                    if is_form and not isinstance(v, str) and validator.is_valid(str(v)):
                        continue
                    # XML text content stringifies primitives; objects/arrays keep structure.
                    if is_xml and isinstance(v, (bool, int, float)) and validator.is_valid(str(v)):
                        continue
                    # Empty dict/None both serialize to empty string in XML
                    if is_xml and (v == {} or v is None) and validator.is_valid(""):
                        continue
                    # `{$ref, sibling}` only honors the ref target on Draft 4 — drop mutations
                    # against the silenced siblings that the bare target accepts vacuously.
                    if sub_has_ref and not is_form and not is_xml and validator.is_valid(v):
                        continue
                inner = value.description or ""
                # Build path notation: "a -> b: leaf" for nested, "a: leaf" for direct
                description = f"{key} -> {inner}" if ": " in inner else f"{key}: {inner}"
                yield NegativeValue(
                    {**template, key: value.value},
                    scenario=value.scenario,
                    description=description,
                    location=nctx.current_path,
                    parameter=key,
                )


def _negative_property_names(
    ctx: CoverageContext, template: dict, property_names_schema: dict
) -> Generator[GeneratedValue, None, None]:
    """Objects with an extra key violating the `propertyNames` sub-schema."""
    nctx = ctx.with_negative()
    for value in cover_schema_iter(nctx, property_names_schema):
        bad_key = value.value
        # JSON object keys are always strings; non-string negatives can't be carried on the wire.
        if not isinstance(bad_key, str) or bad_key in template:
            continue
        candidate = {**template, bad_key: ""}
        if not ctx.leads_to_negative_test_case(candidate):
            continue
        yield NegativeValue(
            candidate,
            scenario=CoverageScenario.OBJECT_INVALID_PROPERTY_NAME,
            description=f"Object with property name violating propertyNames: {value.description}",
            location=nctx.current_path,
        )


def _negative_pattern_properties(
    ctx: CoverageContext, template: dict, pattern_properties: dict
) -> Generator[GeneratedValue, None, None]:
    nctx = ctx.with_negative()
    for pattern, sub_schema in pattern_properties.items():
        compiled = compile_ecma_pattern(pattern)
        if compiled is None:
            continue
        key = ctx.generate_from(st.from_regex(compiled))
        with nctx.at(pattern):
            for value in cover_schema_iter(nctx, sub_schema):
                yield NegativeValue(
                    {**template, key: value.value},
                    scenario=value.scenario,
                    description=f"Object with invalid pattern key '{key}' ('{pattern}') value: {value.description}",
                    location=nctx.current_path,
                )


def _negative_items(
    ctx: CoverageContext, schema: JsonSchema, *, min_items: int = 0
) -> Generator[GeneratedValue, None, None]:
    """Arrays not matching the schema."""
    nctx = ctx.with_negative()
    filler: object = NOT_SET
    # Cap padding at NEGATIVE_MODE_MAX_ITEMS so an adversarial `minItems` doesn't blow up memory;
    # above the cap, fall back to single-item arrays (same as pre-padding behavior for that range).
    if 1 < min_items <= NEGATIVE_MODE_MAX_ITEMS:
        try:
            filler = ctx.with_positive().generate_from_schema(schema)
        except (InvalidArgument, Unsatisfiable):
            # Items schema can't produce a valid filler — fall back to single-item negative
            # rather than emitting nothing.
            pass
    for value in cover_schema_iter(nctx, schema):
        if filler is not NOT_SET:
            # Pad to satisfy `minItems` so the items[i] check fires instead of failing at length.
            items = [value.value, *(filler for _ in range(min_items - 1))]
        else:
            items = [value.value]
        if ctx.leads_to_negative_test_case(items):
            yield NegativeValue(
                items,
                scenario=value.scenario,
                description=f"Array with invalid items: {value.description}",
                location=nctx.current_path,
            )


def _negative_prefix_items(
    ctx: CoverageContext, item_schemas: list[JsonSchema]
) -> Generator[GeneratedValue, None, None]:
    """Arrays with invalid items at specific positions (tuple validation)."""
    if not item_schemas:
        return
    # Generate valid values for each position
    pctx = ctx.with_positive()
    valid_items = []
    for item_schema in item_schemas:
        try:
            valid_items.append(pctx.generate_from_schema(item_schema))
        except (InvalidArgument, Unsatisfiable):
            return
    # For each position, generate negative values and yield arrays with one invalid item
    nctx = ctx.with_negative()
    for idx, item_schema in enumerate(item_schemas):
        for neg_value in cover_schema_iter(nctx, item_schema):
            items = valid_items.copy()
            items[idx] = neg_value.value
            if ctx.leads_to_negative_test_case(items):
                yield NegativeValue(
                    items,
                    scenario=neg_value.scenario,
                    description=f"Array with invalid item at index {idx}: {neg_value.description}",
                    location=nctx.current_path,
                )


def _not_matching_pattern(value: str, pattern: re.Pattern) -> bool:
    return pattern.search(value) is None


def _negative_pattern(
    ctx: CoverageContext, pattern: str, min_length: int | None = None, max_length: int | None = None
) -> Generator[GeneratedValue, None, None]:
    try:
        compiled = re.compile(pattern)
    except re.error:
        return
    # Every string already contains a match, so no value can violate the pattern - searching for
    # one burns the whole generation budget only to come up empty.
    if matches_every_string(pattern):
        return
    # No length fits the window, or none short of the generation buffer, so there is no string to violate it with.
    if (max_length is not None and (min_length or 0) > max_length) or (min_length or 0) >= INTERNAL_BUFFER_SIZE:
        return
    # The same regex recurs verbatim across operations; one Hypothesis search covers the whole audit.
    # `is_valid_for_location` makes the outcome location-dependent, so the location is part of the key.
    cache_key = ("negative_pattern", pattern, min_length, max_length, ctx.location, ctx.validator_cls)
    value = schema_generation_cache.get(cache_key)
    if value is UNSATISFIABLE_RESULT:
        raise Unsatisfiable
    if value is MISSING:
        try:
            validator: jsonschema_rs.Validator | None = ctx.validator_cls(
                {"type": "string", "pattern": pattern}, pattern_options=FANCY_REGEX_OPTIONS
            )
        except Exception:
            validator = None
        strategy = (
            st.text(min_size=min_length or 0, max_size=max_length)
            .filter(partial(_not_matching_pattern, pattern=compiled))
            .filter(ctx.is_valid_for_location)
        )
        if validator is not None:
            strategy = strategy.filter(lambda v, _v=validator: not _v.is_valid(v))
        try:
            value = ctx.generate_from(strategy)
        except Unsatisfiable:
            schema_generation_cache[cache_key] = UNSATISFIABLE_RESULT
            raise
        schema_generation_cache[cache_key] = value
    yield NegativeValue(
        value,
        scenario=CoverageScenario.INVALID_PATTERN,
        description=f"Value not matching the '{pattern}' pattern",
        location=ctx.current_path,
    )


def _with_negated_key(schema: JsonSchemaObject, key: str, value: Any) -> JsonSchemaObject:
    return {"allOf": [{k: v for k, v in schema.items() if k != key}, {"not": {key: value}}]}


def _negative_multiple_of(
    ctx: CoverageContext, schema: dict, multiple_of: int | float
) -> Generator[GeneratedValue, None, None]:
    yield NegativeValue(
        # Only a number can violate `multipleOf`, so say so where the schema left the type open.
        ctx.generate_from_schema(_with_negated_key({"type": "number", **schema}, "multipleOf", multiple_of)),
        scenario=CoverageScenario.NOT_MULTIPLE_OF,
        description=f"Non-multiple of {multiple_of}",
        location=ctx.current_path,
    )


def _negative_unique_items(ctx: CoverageContext, schema: JsonSchemaObject) -> Generator[GeneratedValue, None, None]:
    unique = jsonify(ctx.generate_from_schema({**schema, "type": "array", "minItems": 1, "maxItems": 1}))
    yield NegativeValue(
        unique + unique,
        scenario=CoverageScenario.NON_UNIQUE_ITEMS,
        description="Non-unique items",
        location=ctx.current_path,
    )
    # When the declared type forbids arrays (e.g. Kubernetes paints `uniqueItems: true`
    # onto every scalar query parameter), also emit a 2-element unique-array case so
    # the uniqueItems-valid branch is exercised alongside the duplicate above. Schemas
    # that already admit arrays don't need this — positive generation covers them.
    if "array" not in get_type(schema):
        # Restrict items to scalars so the pair survives round-tripping through repeated
        # query/header/path values; nested objects/arrays collapse into a single slot.
        pair_schema = {
            **schema,
            "type": "array",
            "items": {"type": ["null", "boolean", "string", "number", "integer"]},
            "minItems": 2,
            "maxItems": 2,
            "uniqueItems": True,
        }
        try:
            pair = jsonify(ctx.generate_from_schema(pair_schema))
        except (InvalidArgument, Unsatisfiable):
            return
        if isinstance(pair, list) and len(pair) == 2 and pair[0] != pair[1]:
            yield NegativeValue(
                pair,
                scenario=CoverageScenario.UNIQUE_ITEMS_ARRAY,
                description="Unique items array",
                location=ctx.current_path,
            )


def _negative_required(
    ctx: CoverageContext, template: dict, required: list[str]
) -> Generator[GeneratedValue, None, None]:
    for key in required:
        yield NegativeValue(
            {k: v for k, v in template.items() if k != key},
            scenario=CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY,
            description=f"Missing required property: {key}",
            location=ctx.current_path,
            parameter=key,
        )


def _violates_format(value: object, format: str, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    return not conforms_to_format(value, format, validator_cls)


def _violates_hostname(value: object, validator_cls: type[jsonschema_rs.Validator]) -> bool:
    return value == "" or not conforms_to_format(value, "hostname", validator_cls)


def _negative_format(
    ctx: CoverageContext, schema: JsonSchemaObject, format: str
) -> Generator[GeneratedValue, None, None]:
    # Only generate negative format cases for formats that have validation semantics.
    # In OpenAPI 3.0, `format` is an annotation and does NOT impose validation constraints by itself.
    # Formats like "password" have no validation - any string is valid.
    # We can only generate truly invalid data for formats in VALIDATED_FORMATS (e.g., "email", "uri", "uuid").
    if format not in VALIDATED_FORMATS:
        return
    # The active draft determines which formats actually validate (Draft 4 treats
    # iri-reference / json-pointer / etc. as annotation-only). Skip when the format
    # is not validated — the strategy below would be unsatisfiable for every property.
    validator_cls = ctx.validator_cls
    if format not in VALIDATED_FORMATS_BY_DRAFT.get(validator_cls, frozenset()):
        return
    # Drawing under the format and filtering for values that violate it can never succeed
    without_format = {k: v for k, v in schema.items() if k != "format"}
    without_format["type"] = "string"
    if ctx.location == "path":
        # Empty path parameters are invalid
        without_format["minLength"] = 1
    # Negative-format draws can spend seconds on JS-style `/.../`-wrapped patterns; cache by
    # the structural inputs so the same shape across 100s of operations runs Hypothesis once.
    try:
        cache_key = ("negative_format", ctx.schema_key(without_format), format, validator_cls)
    except (TypeError, ValueError):
        cache_key = None
    if cache_key is not None:
        cached = schema_generation_cache.get(cache_key)
        if cached is UNSATISFIABLE_RESULT:
            raise Unsatisfiable
        if cached is not MISSING:
            yield NegativeValue(
                cached,
                scenario=CoverageScenario.INVALID_FORMAT,
                description=f"Value not matching the '{format}' format",
                location=ctx.current_path,
            )
            return
    if format == "hostname":
        filter_fn = partial(_violates_hostname, validator_cls=validator_cls)
    else:
        filter_fn = partial(_violates_format, format=format, validator_cls=validator_cls)
    try:
        strategy = ctx.build_strategy(without_format)
        if strategy is None:
            raise Unsatisfiable
        value: str = examples.generate_one(strategy.filter(filter_fn))
    except Unsatisfiable:
        if cache_key is not None:
            schema_generation_cache[cache_key] = UNSATISFIABLE_RESULT
        raise
    if cache_key is not None:
        schema_generation_cache[cache_key] = value
    yield NegativeValue(
        value,
        scenario=CoverageScenario.INVALID_FORMAT,
        description=f"Value not matching the '{format}' format",
        location=ctx.current_path,
    )


def _is_non_integer_float(x: float) -> bool:
    return x != int(x)


def _is_not_numeric_string(x: str) -> bool:
    try:
        float(x)
        return False
    except (ValueError, TypeError):
        return True


# Wire values that lenient query/path parsers coerce to a boolean.
BOOLEAN_WIRE_VALUES = frozenset({"0", "1", "true", "false"})


def _is_not_boolean_coercible(x: Any) -> bool:
    return str(x).strip().lower() not in BOOLEAN_WIRE_VALUES


def is_valid_header_value(value: object) -> bool:
    value = str(value)
    if not is_latin_1_encodable(value):
        return False
    if has_invalid_characters("A", value):
        return False
    return True


def jsonify(value: Any) -> Any:
    # Builds a new value: the input may be a spec-declared example that every other case reuses.
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return {key: jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonify(item) for item in value]
    return value


def quote_path_parameter(value: Any) -> str:
    if isinstance(value, str):
        if value == ".":
            return "%2E"
        elif value == "..":
            return "%2E%2E"
        else:
            # Percent-encode for path segments (space -> "%20"); "+" is literal in a path, not a space.
            return quote(value, safe="")
    if isinstance(value, list):
        return ",".join(map(str, value))
    return str(value)


# Far above any text a wrong-type value turns into, so a limit this large rules nothing out.
MAX_STRINGIFIED_TYPE_VIOLATION_LENGTH = 2**20


def _accepts_every_stringified_value(schema: dict[str, Any], types: list[str]) -> bool:
    """Whether this schema accepts the text every wrong-type value turns into."""
    if "string" not in types:
        return False
    for key, value in schema.items():
        if key in _ANNOTATION_KEYWORDS or key in ("type", BUNDLE_STORAGE_KEY):
            continue
        # The shortest violation is a single character, so only a longer minimum can reject one.
        if key == "minLength" and isinstance(value, int) and value <= 1:
            continue
        if key == "maxLength" and isinstance(value, int) and value >= MAX_STRINGIFIED_TYPE_VIOLATION_LENGTH:
            continue
        return False
    return True


def _stringified_type_violations(
    ctx: CoverageContext,
    names: list[str],
    rules: dict[str, list[Callable[[Any], bool]]],
    breaks_the_schema: Callable[[Any], bool],
) -> list[Any]:
    """One value per wrong type whose rendering the schema turns down.

    Only text reaches the server here, so whether a rendering breaks the schema is a question one
    member answers for the whole type - no need to draw for it.
    """
    values = []
    for name in names:
        for candidate in STRINGIFIED_TYPE_PROBES[name]:
            if not all(rule(candidate) for rule in rules.get(name, ())):
                continue
            if isinstance(candidate, (dict, list)):
                candidate = deepclone(candidate)
            if ctx.location == ParameterLocation.PATH:
                candidate = quote_path_parameter(jsonify(candidate))
            elif ctx.location == ParameterLocation.QUERY:
                candidate = jsonify(candidate)
            if breaks_the_schema(candidate):
                values.append(candidate)
                break
    return values


def _negative_type(
    ctx: CoverageContext, ty: str | list[str], seen: HashSet, schema: dict[str, Any]
) -> Generator[GeneratedValue, None, None]:
    if isinstance(ty, str):
        types = [ty]
    else:
        types = ty
    # Root-level binary/byte format with non-JSON content types - type mutations don't produce meaningful wire violations
    # Path is ['type'] at root level, vs ['properties', 'fieldname', 'type'] for nested properties
    if (
        "string" in types
        and ctx.location == ParameterLocation.BODY
        and schema.get("format") in ("binary", "byte")
        and ctx.path == ["type"]
        and ctx.media_type is not None
        and ctx.media_type[1] != "json"
    ):
        return
    # Form/multipart body-level type mutations don't yield reliable wire violations:
    # form-urlencoded serializes to empty body; multipart renders as boundaries around
    # str(value), which permissive servers accept as zero-part multipart.
    if "object" in types and ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type):
        return
    # Form-parts stringify every value; non-strings sent for a string-typed property
    # read as valid strings server-side, collapsing into the enum/format/range negation.
    if "string" in types and ctx.location == ParameterLocation.BODY and is_form_parts(ctx.media_type):
        return
    # Same parameter shape recurs across many operations; one Hypothesis draw covers the whole audit.
    # `ctx.path` is intentionally absent: the cached values are path-agnostic — the JSON pointer
    # only stamps `NegativeValue.location` at yield time below.
    try:
        cache_key = (
            "negative_type",
            tuple(sorted(types)),
            ctx.schema_key(schema),
            ctx.location,
            ctx.media_type,
            ctx.validator_cls,
        )
    except (TypeError, ValueError):
        cache_key = None
    if cache_key is not None:
        cached = schema_generation_cache.get(cache_key)
        if cached is not MISSING:
            for value in cached:
                if seen.insert(value) and ctx.is_valid_for_location(value):
                    yield NegativeValue(
                        value,
                        scenario=CoverageScenario.INCORRECT_TYPE,
                        description="Incorrect type",
                        location=ctx.current_path,
                    )
            return
    strategies = {ty: strategy for ty, strategy in STRATEGIES_FOR_TYPE.items() if ty not in types}
    if "string" in strategies:
        strategies["string"] = NEGATIVE_STRING_STRATEGY
    # Rules kept per type, so a probe can be held to the same ones without drawing.
    rules: dict[str, list[Callable[[Any], bool]]] = {}

    def restrict(name: str, rule: Callable[[Any], bool]) -> None:
        strategies[name] = strategies[name].filter(rule)
        rules.setdefault(name, []).append(rule)

    filter_func = {
        "path": lambda x: not is_invalid_path_parameter(x),
        "header": is_valid_header_value,
        "cookie": is_valid_header_value,
        "query": lambda x: not contains_unicode_surrogate_pair(x),
    }.get(ctx.location)

    if "number" in types:
        strategies.pop("integer", None)
    elif "integer" in types:
        # A non-integer float breaks `integer`; with `number` also allowed it would be a valid value.
        strategies["number"] = FLOAT_STRATEGY
        restrict("number", _is_non_integer_float)
    # For path/query parameters, numeric strings like "9" serialize identically to integer 9 in the URL,
    # making them indistinguishable and causing false positive failures
    if ctx.location in (ParameterLocation.PATH, ParameterLocation.QUERY) and ("integer" in types or "number" in types):
        if "string" in strategies:
            restrict("string", _is_not_numeric_string)
    # For path/query parameters, 0/1/true/false serialize to wire values lenient parsers
    # accept as booleans, making them indistinguishable from a valid boolean.
    if ctx.location in (ParameterLocation.PATH, ParameterLocation.QUERY) and "boolean" in types:
        for ty in ("integer", "number", "string"):
            if ty in strategies:
                restrict(ty, _is_not_boolean_coercible)
    if ctx.location in (ParameterLocation.QUERY, ParameterLocation.PATH):
        strategies.pop("object", None)
    # Form-urlencoded property-level mutations with null/array/object serialize to empty
    if ctx.location == ParameterLocation.BODY and ctx.media_type == ("application", "x-www-form-urlencoded"):
        strategies.pop("null", None)
        strategies.pop("array", None)
        strategies.pop("object", None)
    # XML body: null and empty string both serialize to an empty element (<RootTag></RootTag>),
    # indistinguishable from an empty object {} at the wire level
    if (
        "object" in types
        and ctx.location == ParameterLocation.BODY
        and ctx.media_type is not None
        and is_xml_parts(ctx.media_type)
    ):
        strategies.pop("null", None)
        strategies.pop("string", None)
    if filter_func is not None:
        for ty in list(strategies):
            restrict(ty, filter_func)

    pattern = schema.get("pattern")
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error:
            schema = schema.copy()
            del schema["pattern"]
            return

    if isinstance(schema, dict) and BUNDLE_STORAGE_KEY in ctx.root_schema:
        schema = dict(schema)
        schema[BUNDLE_STORAGE_KEY] = ctx.root_schema[BUNDLE_STORAGE_KEY]

    schema = _remove_examples(schema)

    try:
        is_valid = make_validator(schema, ctx.validator_cls).is_valid
        is_valid(None)
        apply_validation = True
    except Exception:
        # Schema is not correct and we can't validate the generated instances.
        # In such a scenario it is better to generate at least something with some chances to have a false
        # positive failure
        apply_validation = False

        def is_valid(x: object) -> bool:
            return True

    def _does_not_match_the_original_schema(value: Any) -> bool:
        # For XML, None serializes to "" (empty element content), not to "None"
        if ctx.media_type is not None and is_xml_parts(ctx.media_type) and value is None:
            return not is_valid("")
        return not is_valid(str(value))

    if ctx.location == ParameterLocation.PATH:
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify).map(quote_path_parameter)
    elif ctx.location == ParameterLocation.QUERY:
        for ty, strategy in strategies.items():
            strategies[ty] = strategy.map(jsonify)

    # Materialize before yielding so the cache fills even when the consumer stops mid-iteration.
    generated_values: list[Any] = []
    if apply_validation and ctx.will_be_serialized_to_string():
        if _accepts_every_stringified_value(schema, types):
            # Nothing here could break the schema once it reaches the wire as text.
            return
        generated_values = _stringified_type_violations(
            ctx, list(strategies), rules, _does_not_match_the_original_schema
        )
    else:
        for strategy in strategies.values():
            try:
                generated_values.append(ctx.generate_from(strategy))
            except Unsatisfiable:
                break
    if cache_key is not None:
        schema_generation_cache[cache_key] = generated_values
    for value in generated_values:
        if seen.insert(value) and ctx.is_valid_for_location(value):
            yield NegativeValue(
                value, scenario=CoverageScenario.INCORRECT_TYPE, description="Incorrect type", location=ctx.current_path
            )


def _flip_generation_mode_for_not(
    values: Generator[GeneratedValue, None, None],
) -> Generator[GeneratedValue, None, None]:
    """Flip generation mode for values from 'not' schemas.

    For 'not' schemas, the semantic is inverted:
    - Positive values for the inner schema are negative for the outer schema
    - Negative values for the inner schema are positive for the outer schema
    """
    for value in values:
        flipped_mode = (
            GenerationMode.NEGATIVE if value.generation_mode == GenerationMode.POSITIVE else GenerationMode.POSITIVE
        )
        yield GeneratedValue(
            value=value.value,
            generation_mode=flipped_mode,
            scenario=value.scenario,
            description=value.description,
            location=value.location,
            parameter=value.parameter,
        )


def push_examples_to_properties(schema: JsonSchemaObject) -> None:
    """Push examples from the top-level 'examples' field to the corresponding properties."""
    if "examples" in schema and "properties" in schema:
        properties = schema["properties"]
        for example in schema["examples"]:
            if isinstance(example, dict):
                for prop, value in example.items():
                    if prop in properties and isinstance(properties[prop], dict):
                        if "examples" not in properties[prop]:
                            properties[prop]["examples"] = []
                        if value not in properties[prop]["examples"]:
                            properties[prop]["examples"].append(value)
