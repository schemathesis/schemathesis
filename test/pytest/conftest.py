import pytest
from hypothesis import settings


@pytest.fixture
def reload_profile():
    # Loading a Hypothesis profile in a pytester-style test overrides it globally.
    previous = settings.get_current_profile_name()
    yield
    settings.load_profile(previous)
