## Fixing the bug

The schema accepts any string for `room_type`, but `app.py` defines prices only for `standard`, `deluxe`, and `suite`. Any other value raises `KeyError`, which FastAPI turns into a 500 response.

Constrain the field in `examples/booking/app.py`, so FastAPI rejects unknown room types with a 422 before your code runs:

=== "Before (broken)"
    ```python
    class BookingRequest(BaseModel):
        room_type: str  # Any string allowed!
    ```

=== "After (fixed)"
    ```python
    from enum import Enum


    class RoomType(str, Enum):
        standard = "standard"
        deluxe = "deluxe"
        suite = "suite"


    class BookingRequest(BaseModel):
        room_type: RoomType  # Only valid values
    ```

The container image copies `app.py` during the build, so rebuild it to apply the change:

```console
docker compose up -d --build
```
