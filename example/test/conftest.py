from hypothesis import settings

# Load Schemathesis hooks
from .hooks import *  # noqa: F403

settings.register_profile("CI", max_examples=1000)
