from __future__ import annotations


def from_parameter(parameter: str, path: str) -> str | None:
    parameter = parameter.strip()
    lower = parameter.lower()

    if lower == "id":
        return from_path(path, parameter_name=parameter)

    # Capital-sensitive
    capital_suffixes = ("Id", "Uuid", "Guid")
    for suffix in capital_suffixes:
        if parameter.endswith(suffix):
            prefix = parameter[: -len(suffix)]
            if len(prefix) >= 2:
                return to_pascal_case(prefix)

    # Snake_case (case-insensitive is fine here)
    # Composite suffixes first (longer patterns match before shorter ones)
    snake_suffixes = (
        "_id_or_slug",
        "-id-or-slug",
        "_guid",
        "_uuid",
        "_id",
        "_slug",
        "-guid",
        "-uuid",
        "-id",
        "-slug",
    )
    for suffix in snake_suffixes:
        if lower.endswith(suffix):
            prefix = parameter[: -len(suffix)]
            if len(prefix) >= 2:
                return to_pascal_case(prefix)

    # Special cases that need exact match
    # Twilio-style, capital S
    if parameter.endswith("Sid"):
        prefix = parameter[:-3]
        if len(prefix) >= 2:
            return to_pascal_case(prefix)

    return None


def from_path(path: str, parameter_name: str | None = None) -> str | None:
    """Detect resource name from OpenAPI path."""
    segments = [s for s in path.split("/") if s]

    if not segments:
        # API Root
        return None

    # If parameter name provided, find the resource it refers to
    if parameter_name:
        placeholder = f"{{{parameter_name}}}"
        try:
            param_index = segments.index(placeholder)
            if param_index > 0:
                resource_segment = segments[param_index - 1]
                if "{" not in resource_segment:
                    singular = to_singular(resource_segment)
                    return to_pascal_case(singular)
        except ValueError:
            pass  # Parameter not found in path

    # Fallback to last non-parameter segment
    non_param_segments = [s for s in segments if "{" not in s]
    if non_param_segments:
        last_segment = non_param_segments[-1]
        # Handle special suffixes that refer to "current instance" of parent resource
        # e.g., /groups/self -> Group, /users/me -> User, /accounts/current -> Account
        if last_segment.lower() in ("self", "me", "current") and len(non_param_segments) > 1:
            last_segment = non_param_segments[-2]
        singular = to_singular(last_segment)
        return to_pascal_case(singular)

    return None


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


def _is_word_like(s: str) -> bool:
    """Check if string looks like a word (not a path, technical term, etc)."""
    # Skip empty or very short
    if not s or len(s) < 2:
        return False
    # Skip if contains non-word characters (except underscore and hyphen)
    if not all(c.isalpha() or c in ("_", "-") for c in s):
        return False
    # Skip if has numbers
    return not any(c.isdigit() for c in s)


def to_singular(word: str) -> str:
    if not _is_word_like(word):
        return word
    if word.lower() in UNCOUNTABLE:
        return word
    known_lower = IRREGULAR_TO_SINGULAR.get(word.lower())
    if known_lower is not None:
        # Preserve case: if input was capitalized, capitalize result
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
    # Handle "ses" ending: check if it was "se" + "s" or "s" + "es"
    if word.endswith("ses") and len(word) > 3:
        # "gases" has 's' at position -3, formed from "gas" + "es"
        # "statuses" has 's' at position -3, formed from "status" + "es"
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


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
    # Only change y -> ies after consonants (party -> parties, not day -> days)
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    # class -> classes
    if word.endswith("ss"):
        return word + "es"
    # words that normally take -es: box -> boxes
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    # just add 's' (car -> cars)
    return word + "s"


def to_pascal_case(text: str) -> str:
    # snake_case/kebab-case - split and capitalize each word
    if "_" in text or "-" in text:
        parts = text.replace("-", "_").split("_")
        return "".join(word.capitalize() for word in parts if word)
    # camelCase - just uppercase first letter, preserve the rest
    return text[0].upper() + text[1:] if text else text


def to_snake_case(text: str) -> str:
    text = text.replace("-", "_")
    # Insert underscores before uppercase letters
    result = []
    for i, char in enumerate(text):
        # Add underscore before uppercase (except at start)
        if i > 0 and char.isupper():
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def find_matching_field(*, parameter: str, resource: str, fields: list[str]) -> str | None:
    """Find which resource field matches the parameter name."""
    if not fields:
        return None

    # Exact match
    if parameter in fields:
        return parameter

    # Normalize for fuzzy matching
    parameter_normalized = _normalize_for_matching(parameter)
    resource_normalized = _normalize_for_matching(resource)

    # Normalized exact match
    # `brandId` -> `Brand.BrandId`
    for field in fields:
        if _normalize_for_matching(field) == parameter_normalized:
            return field

    # Extract parameter components
    parameter_prefix, parameter_suffix = _split_parameter_name(parameter)
    parameter_prefix_normalized = _normalize_for_matching(parameter_prefix)

    # Parameter has resource prefix, field might not
    # Example: `channelId` - `Channel.id`
    if parameter_prefix and parameter_prefix_normalized == resource_normalized:
        suffix_normalized = _normalize_for_matching(parameter_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == suffix_normalized:
                return field

    # Parameter has no prefix, field might have resource prefix
    # Example: `id` - `Channel.channelId`
    if not parameter_prefix and parameter_suffix:
        expected_field_normalized = resource_normalized + _normalize_for_matching(parameter_suffix)

        for field in fields:
            field_normalized = _normalize_for_matching(field)
            if field_normalized == expected_field_normalized:
                return field

    # ID field synonym matching (for identifier parameters)
    # Match parameter like 'conversation_id' or 'id' with fields like 'uuid', 'guid', 'uid'
    parameter_prefix, parameter_suffix = _split_parameter_name(parameter)
    suffix_normalized = _normalize_for_matching(parameter_suffix)

    # Common identifier field names in priority order
    ID_FIELD_NAMES = ["id", "uuid", "guid", "uid"]
    SLUG_FIELD_NAMES = ["slug"]

    # Handle composite suffixes like `_id_or_slug` - try ID fields first, then slug
    if suffix_normalized == "idorslug":
        for id_name in ID_FIELD_NAMES + SLUG_FIELD_NAMES:
            for field in fields:
                if _normalize_for_matching(field) == id_name:
                    return field
    elif suffix_normalized in ID_FIELD_NAMES:
        # Try to match with any identifier field, preferring exact match first
        for id_name in ID_FIELD_NAMES:
            for field in fields:
                if _normalize_for_matching(field) == id_name:
                    return field

    return None


def _normalize_for_matching(text: str) -> str:
    """Normalize text for case-insensitive, separator-insensitive matching.

    Examples:
        "channelId" -> "channelid"
        "channel_id" -> "channelid"
        "ChannelId" -> "channelid"
        "Channel" -> "channel"

    """
    return text.lower().replace("_", "").replace("-", "")


def _split_parameter_name(parameter_name: str) -> tuple[str, str]:
    """Split parameter into (prefix, suffix) components.

    Examples:
        "channelId" -> ("channel", "Id")
        "userId" -> ("user", "Id")
        "user_id" -> ("user", "_id")
        "id" -> ("", "id")
        "channel_id" -> ("channel", "_id")
        "league_id_or_slug" -> ("league", "_id_or_slug")

    """
    if parameter_name.endswith("Id") and len(parameter_name) > 2:
        return (parameter_name[:-2], "Id")

    # Composite suffixes first (longer patterns before shorter ones)
    if parameter_name.endswith("_id_or_slug") and len(parameter_name) > 11:
        return (parameter_name[:-11], "_id_or_slug")

    if parameter_name.endswith("-id-or-slug") and len(parameter_name) > 11:
        return (parameter_name[:-11], "-id-or-slug")

    if parameter_name.endswith("_id") and len(parameter_name) > 3:
        return (parameter_name[:-3], "_id")

    if parameter_name.endswith("_guid") and len(parameter_name) > 5:
        return (parameter_name[:-5], "_guid")

    if parameter_name.endswith("_slug") and len(parameter_name) > 5:
        return (parameter_name[:-5], "_slug")

    return ("", parameter_name)


def strip_affixes(name: str, prefixes: list[str], suffixes: list[str]) -> str:
    """Remove common prefixes and suffixes from a name (case-insensitive)."""
    result = name.strip()
    name_lower = result.lower()

    # Remove one matching prefix
    for prefix in prefixes:
        if name_lower.startswith(prefix):
            result = result[len(prefix) :]
            break

    # Remove one matching suffix
    for suffix in suffixes:
        if name_lower.endswith(suffix):
            result = result[: -len(suffix)]
            break

    return result.strip()
