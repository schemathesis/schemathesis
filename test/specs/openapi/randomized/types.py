from typing import Annotated

from hypothesis import strategies as st


class Pattern:
    def __class_getitem__(cls, pattern: str):
        return Annotated[str, st.from_regex(pattern)]


class UniqueList:
    def __class_getitem__(cls, inner: type):
        return Annotated[list, st.lists(st.from_type(inner), unique=True)]


class CombinedDict:
    def __class_getitem__(cls, args):
        keys, values, defaults = args

        def update(d):
            d.update(defaults)
            return d

        return Annotated[dict, st.dictionaries(st.from_type(keys), st.from_type(values)).map(update)]


class Missing:
    pass
