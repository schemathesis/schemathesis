from enum import Enum


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    SKIP = "skip"
