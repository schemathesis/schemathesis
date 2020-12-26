import attr


@attr.s(slots=True)
class Booking:
    id: int = attr.ib()
    name: str = attr.ib()
    is_active: bool = attr.ib()

    asdict = attr.asdict
