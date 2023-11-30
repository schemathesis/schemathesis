from hypothesis import settings

# Load Schemathesis hooks
from .extensions import *  # noqa: F403

# Custom testing profile
settings.register_profile("CI", max_examples=1000)
