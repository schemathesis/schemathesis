import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Booking API", version="1.0.0")

BOOKINGS: dict[str, dict] = {}


def verify_token(authorization: str | None = Header(None)) -> bool:
    if not authorization or authorization != "Bearer secret-token":
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return True


class BookingRequest(BaseModel):
    guest_name: str = Field(min_length=2, max_length=100)
    room_type: str
    nights: int = Field(gt=0, le=365)


class BookingResponse(BaseModel):
    booking_id: str
    guest_name: str
    room_type: str
    nights: int
    status: str
    price_per_night: float
    total_price: float


@app.post("/bookings", response_model=BookingResponse, responses={400: {"description": "Invalid booking"}})  # type: ignore[untyped-decorator]
def create_booking(booking: BookingRequest, _: bool = Depends(verify_token)) -> BookingResponse:
    # Calculate price based on room type
    room_prices = {"standard": 99.99, "deluxe": 149.99, "suite": 299.99}

    price_per_night = room_prices[booking.room_type]
    total_price = price_per_night * booking.nights

    booking_id = str(uuid.uuid4())
    booking_data = {
        "booking_id": booking_id,
        "guest_name": booking.guest_name,
        "room_type": booking.room_type,
        "nights": booking.nights,
        "status": "confirmed",
        "price_per_night": price_per_night,
        "total_price": total_price,
    }
    BOOKINGS[booking_id] = booking_data
    return BookingResponse(**booking_data)


@app.get(  # type: ignore[untyped-decorator]
    "/bookings/{booking_id}", response_model=BookingResponse, responses={404: {"description": "Booking not found"}}
)
def get_booking(booking_id: str, _: bool = Depends(verify_token)) -> BookingResponse:
    if booking_id not in BOOKINGS:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingResponse(**BOOKINGS[booking_id])


@app.get("/health")  # type: ignore[untyped-decorator]
def health_check() -> dict:
    return {"status": "healthy"}
