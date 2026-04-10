import jsonschema_rs

from .bundler import (
    BUNDLE_STORAGE_KEY,
    REFERENCE_TO_BUNDLE_PREFIX,
    BundleCache,
    BundleError,
    Bundler,
    bundle,
    prepare_for_generation,
    prepare_for_validation,
    unbundle,
    unbundle_path,
)
from .keywords import ALL_KEYWORDS
from .types import get_type

# Support lookahead/lookbehind assertions common in ECMA-262 patterns,
# with a large size limit to handle schemas with large quantifiers (e.g., {1,51200})
FANCY_REGEX_OPTIONS = jsonschema_rs.FancyRegexOptions(size_limit=1_000_000_000)

__all__ = [
    "ALL_KEYWORDS",
    "bundle",
    "BundleCache",
    "Bundler",
    "BundleError",
    "FANCY_REGEX_OPTIONS",
    "prepare_for_generation",
    "prepare_for_validation",
    "REFERENCE_TO_BUNDLE_PREFIX",
    "BUNDLE_STORAGE_KEY",
    "get_type",
    "unbundle",
    "unbundle_path",
]
