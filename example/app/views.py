from aiohttp import web

from . import db


async def get_booking_by_id(request: web.Request, booking_id: int) -> web.Response:
    booking = await db.get_booking_by_id(request.app["db"], booking_id=booking_id)
    if booking is not None:
        data = booking.asdict()
    else:
        data = {}
    return web.json_response(data)


async def get_bookings(request: web.Request, limit: int = 20) -> web.Response:
    bookings = await db.get_bookings(request.app["db"], limit=limit)
    return web.json_response([b.asdict() for b in bookings])


async def create_booking(request: web.Request, body) -> web.Response:
    booking = await db.create_booking(
        request.app["db"],
        booking_id=body["id"],
        name=body["name"],
        is_active=body["is_active"],
    )
    return web.json_response(booking.asdict(), status=201)
