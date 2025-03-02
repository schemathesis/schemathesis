import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode


class ConfigSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT

    def serialize(self, data, **kwargs) -> str:
        if isinstance(data, str):
            return data
        return repr(data)


@pytest.fixture
def snapshot_config(snapshot):
    return snapshot.use_extension(ConfigSnapshotExtension)
