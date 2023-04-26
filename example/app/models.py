from dataclasses import dataclass, asdict as _asdict


@dataclass
class Booking:
    id: int
    name: str
    is_active: bool

    asdict = _asdict
