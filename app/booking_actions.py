from datetime import date

from sqlalchemy import text

from db import engine


class BookingError(Exception):
    """Raised for any booking/cancellation failure the caller should see."""


def _generate_booking_id(conn) -> str:
    """
    Generates the next booking_id in the existing 'BK000001' format,
    using a real Postgres sequence so concurrent bookings can't collide
    on the same ID. Creates the sequence on first use, seeded past the
    highest existing numeric ID already in room_bookings.
    """
    conn.execute(text("""
        CREATE SEQUENCE IF NOT EXISTS room_booking_id_seq
    """))

    # One-time seed: if the sequence is brand new (last_value defaults to 1
    # and has never been called), align it past the existing max booking_id
    # so newly generated IDs don't collide with seeded data.
    seq_info = conn.execute(text(
        "SELECT last_value, is_called FROM room_booking_id_seq"
    )).fetchone()
    if not seq_info.is_called:
        max_existing = conn.execute(text(
            "SELECT COALESCE(MAX(CAST(SUBSTRING(booking_id FROM 3) AS INTEGER)), 0) "
            "FROM room_bookings WHERE booking_id ~ '^BK[0-9]+$'"
        )).scalar()
        conn.execute(text("SELECT setval('room_booking_id_seq', :start)"),
                     {"start": max_existing + 1})

    next_val = conn.execute(text("SELECT nextval('room_booking_id_seq')")).scalar()
    return f"BK{next_val:06d}"


def check_availability(room_type: str, check_in: date, check_out: date) -> list[dict]:
    """
    Returns a list of candidate rooms (room_number, total price for the
    stay) that are available for every night of [check_in, check_out).
    Read-only — safe to call as often as needed while a guest is deciding.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise BookingError("Check-out must be after check-in.")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.room_number, ra.date, ra.price
            FROM rooms r
            JOIN room_availability ra ON ra.room_id = r.room_id
            WHERE r.type ILIKE :room_type
              AND ra.date >= :check_in AND ra.date < :check_out
              AND ra.status ILIKE 'available'
            ORDER BY r.room_number, ra.date
        """), {"room_type": f"%{room_type}%", "check_in": check_in, "check_out": check_out}).fetchall()

    # Group by room, keep only rooms with a price row for every night.
    by_room: dict[int, list[float]] = {}
    for row in rows:
        by_room.setdefault(row.room_number, []).append(row.price)

    candidates = [
        {"room_number": room_number, "total_price": round(sum(prices), 2)}
        for room_number, prices in by_room.items()
        if len(prices) == nights
    ]
    return sorted(candidates, key=lambda c: c["total_price"])


def create_booking(
    customer_id: int,
    room_number: int,
    check_in: date,
    check_out: date,
    num_adults: int = 1,
    num_children: int = 0,
    special_requests: str | None = None,
) -> dict:
    """
    Creates a booking. Re-checks and locks availability atomically as
    part of the same transaction that marks the dates as booked, so a
    concurrent booking on the same room/dates can't slip through.
    Raises BookingError if the room isn't available for the full range.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        raise BookingError("Check-out must be after check-in.")
    if num_adults < 1:
        raise BookingError("A booking needs at least one adult.")

    with engine.begin() as conn:  # begin() = transaction, auto-commits on success, rolls back on exception
        # Get the price for each night before flipping status, so we can
        # compute the total even after the rows say 'Booked'.
        price_rows = conn.execute(text("""
            SELECT ra.date, ra.price
            FROM room_availability ra
            JOIN rooms r ON r.room_id = ra.room_id
            WHERE r.room_number = :room_number
              AND ra.date >= :check_in AND ra.date < :check_out
              AND ra.status ILIKE 'available'
        """), {"room_number": room_number, "check_in": check_in, "check_out": check_out}).fetchall()

        if len(price_rows) != nights:
            raise BookingError(
                f"Room {room_number} is not available for all nights of "
                f"{check_in} to {check_out}."
            )
        total_price = round(sum(r.price for r in price_rows), 2)

        # Atomic conditional update: only flips rows that are STILL
        # 'Available' at the moment this runs. If another transaction
        # booked one of these nights in between our SELECT above and
        # this UPDATE, rowcount will be less than `nights`, and we roll
        # back rather than proceeding with a partial/incorrect booking.
        result = conn.execute(text("""
            UPDATE room_availability ra
            SET status = 'Booked'
            FROM rooms r
            WHERE ra.room_id = r.room_id
              AND r.room_number = :room_number
              AND ra.date >= :check_in AND ra.date < :check_out
              AND ra.status ILIKE 'available'
        """), {"room_number": room_number, "check_in": check_in, "check_out": check_out})

        if result.rowcount != nights:
            raise BookingError(
                f"Room {room_number} was just booked by someone else for "
                f"these dates. Please try a different room or date range."
            )

        room_type = conn.execute(text(
            "SELECT type FROM rooms WHERE room_number = :room_number"
        ), {"room_number": room_number}).scalar()

        booking_id = _generate_booking_id(conn)
        # Placeholder loyalty-points rule (roughly 2x spend, matching the
        # pattern in the seeded data) — confirm the real business rule
        # with whoever owns the loyalty program before relying on this.
        points_earned = round(total_price * 2)

        conn.execute(text("""
            INSERT INTO room_bookings (
                booking_id, customer_id, room_number, room_type,
                check_in, check_out, duration_days, num_adults, num_children,
                special_requests, booking_status, total_amount, points_earned
            ) VALUES (
                :booking_id, :customer_id, :room_number, :room_type,
                :check_in, :check_out, :nights, :num_adults, :num_children,
                :special_requests, 'Confirmed', :total_price, :points_earned
            )
        """), {
            "booking_id": booking_id, "customer_id": customer_id,
            "room_number": room_number, "room_type": room_type,
            "check_in": check_in, "check_out": check_out, "nights": nights,
            "num_adults": num_adults, "num_children": num_children,
            "special_requests": special_requests, "total_price": total_price,
            "points_earned": points_earned,
        })

    return {
        "booking_id": booking_id,
        "room_number": room_number,
        "check_in": str(check_in),
        "check_out": str(check_out),
        "total_price": total_price,
        "points_earned": points_earned,
    }


def cancel_booking(booking_id: str, customer_id: int) -> dict:
    """
    Cancels a booking. Requires customer_id to match the booking's owner
    — this is the authorization check preventing one guest from
    cancelling another guest's reservation. Frees up the room's dates
    back to 'Available'.
    """
    with engine.begin() as conn:
        booking = conn.execute(text("""
            SELECT booking_id, customer_id, room_number, check_in, check_out, booking_status
            FROM room_bookings
            WHERE booking_id = :booking_id
        """), {"booking_id": booking_id}).fetchone()

        if booking is None:
            raise BookingError(f"No booking found with ID {booking_id}.")

        if booking.customer_id != customer_id:
            raise BookingError(
                "This booking does not belong to the requesting guest."
            )

        if booking.booking_status.lower() == "cancelled":
            raise BookingError(f"Booking {booking_id} is already cancelled.")

        conn.execute(text("""
            UPDATE room_bookings SET booking_status = 'Cancelled'
            WHERE booking_id = :booking_id
        """), {"booking_id": booking_id})

        conn.execute(text("""
            UPDATE room_availability ra
            SET status = 'Available'
            FROM rooms r
            WHERE ra.room_id = r.room_id
              AND r.room_number = :room_number
              AND ra.date >= :check_in AND ra.date < :check_out
              AND ra.status ILIKE 'booked'
        """), {
            "room_number": booking.room_number,
            "check_in": booking.check_in,
            "check_out": booking.check_out,
        })

    return {
        "booking_id": booking_id,
        "status": "Cancelled",
        "room_number": booking.room_number,
    }