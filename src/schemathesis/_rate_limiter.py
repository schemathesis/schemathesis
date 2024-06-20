from ._dependency_versions import IS_PYRATE_LIMITER_ABOVE_3

if IS_PYRATE_LIMITER_ABOVE_3:
    from pyrate_limiter import Limiter, Rate, RateItem
else:
    from pyrate_limiter import Limiter
    from pyrate_limiter import RequestRate as Rate
