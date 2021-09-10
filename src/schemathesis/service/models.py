import attr


@attr.s(slots=True)
class TestJob:
    job_id: str = attr.ib()
    short_url: str = attr.ib()
