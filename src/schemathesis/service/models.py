import attr


@attr.s(slots=True)
class TestRun:
    run_id: str = attr.ib()
    short_url: str = attr.ib()
