from enum import Enum, unique

DEFAULT_TARGETS = ()
DEFAULT_TARGETS_NAMES = ()


@unique
class Target(Enum):
    response_time = 1
