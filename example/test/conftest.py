from hypothesis import settings

from .hooks import *  # load Schemathesis hooks

settings.register_profile("CI", max_examples=1000)
