import uuid
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Booking API", version="1.0.0")

BOOKINGS = {}


class BookingRequest(BaseModel):
    guest_name: str
    room_type: str
    nights: int


class BookingResponse(BaseModel):
    booking_id: str
    guest_name: str
    room_type: str
    nights: int
    status: str


def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or authorization != "Bearer secret-token":
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return True


def is_valid_name(name: str) -> bool:
    # Initial buggy validation - will fail on names with multiple spaces
    try:
        first, last = name.split(" ")
        return bool(first and last)
    except ValueError:
        return False


@app.post("/bookings", response_model=BookingResponse)
def create_booking(booking: BookingRequest, _: bool = Depends(verify_token)):
    # Bug 1: No validation - will cause DB-like errors on edge cases
    # Uncomment the next lines after first fix:
    # if not is_valid_name(booking.guest_name):
    #     raise HTTPException(status_code=400, detail="Invalid guest name format")

    # Simulate database constraint failure on certain inputs
    if not booking.guest_name.strip():
        raise HTTPException(status_code=500, detail="Database constraint violation")

    booking_id = str(uuid.uuid4())
    booking_data = {
        "booking_id": booking_id,
        "guest_name": booking.guest_name,
        "room_type": booking.room_type,
        "nights": booking.nights,
        "status": "confirmed",
    }
    BOOKINGS[booking_id] = booking_data
    return BookingResponse(**booking_data)


@app.get("/bookings/{booking_id}", response_model=BookingResponse)
def get_booking(booking_id: str, _: bool = Depends(verify_token)):
    if booking_id not in BOOKINGS:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingResponse(**BOOKINGS[booking_id])


@app.get("/health")
def health_check():
    return {"status": "healthy"}
