import pytest

from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.specs.openapi.negative.mutations import _materialize_one


@pytest.fixture
def materialize_targets():
    # Replays every descriptor the way the negative-mutation strategy does, one at a time.
    def materialize(new_schema, descriptors):
        bundle = new_schema.get(BUNDLE_STORAGE_KEY)
        bundle_map = bundle if isinstance(bundle, dict) else {}
        targets = [_materialize_one(new_schema, descriptor, bundle_map) for descriptor in descriptors]
        return [target for target in targets if target is not None]

    return materialize
