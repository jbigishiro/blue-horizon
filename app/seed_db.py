"""
Creates all tables and seeds them with sample data so you have
something realistic to query while building the AI layers.

Usage:
    python seed_db.py

Once you have your actual dataset exported as CSVs, replace the
hardcoded lists below with pandas.read_csv(...) loads instead.
"""

from datetime import date, timedelta
import random

from db import engine, SessionLocal
from models import Base, Guest, RoomType, Room, Pricing, Booking, Amenity, FAQ

# --- 1. Create tables -------------------------------------------------

Base.metadata.create_all(engine)

db = SessionLocal()

# --- 2. Room types ------------------------------------------------------

room_types_data = [
    dict(name="Standard King", description="Cozy room with a king bed and city view.",
         max_occupancy=2, base_price=180, amenities_summary="WiFi, Smart TV, Coffee Maker"),
    dict(name="Deluxe Twin", description="Spacious room with two queen beds, ideal for families.",
         max_occupancy=4, base_price=240, amenities_summary="WiFi, Smart TV, Minibar, Balcony"),
    dict(name="Executive Suite", description="Separate living area with premium furnishings and skyline view.",
         max_occupancy=3, base_price=420, amenities_summary="WiFi, Smart TV, Minibar, Balcony, Nespresso Bar"),
    dict(name="Ocean Suite", description="Top-floor suite with private balcony and direct ocean view.",
         max_occupancy=4, base_price=650, amenities_summary="WiFi, Smart TV, Minibar, Private Balcony, Jacuzzi"),
    dict(name="Presidential Suite", description="The hotel's most luxurious accommodation with butler service.",
         max_occupancy=6, base_price=1200, amenities_summary="WiFi, Smart TV, Full Bar, Private Terrace, Butler Service"),
]

room_types = [RoomType(**data) for data in room_types_data]
db.add_all(room_types)
db.commit()

# --- 3. Rooms (link physical rooms to room types) ------------------------

rooms = []
room_number = 100
for rt in room_types:
    num_rooms = {"Standard King": 20, "Deluxe Twin": 15,
                 "Executive Suite": 8, "Ocean Suite": 5,
                 "Presidential Suite": 2}[rt.name]
    for i in range(num_rooms):
        floor = 1 + (room_number // 100) % 10
        rooms.append(Room(room_number=str(room_number), floor=floor, room_type_id=rt.id))
        room_number += 1
    room_number = ((room_number // 100) + 1) * 100  # jump to next floor block

db.add_all(rooms)
db.commit()

# --- 4. Pricing (simple seasonal example: higher prices in summer) -------

pricing_rows = []
for rt in room_types:
    pricing_rows.append(Pricing(
        room_type_id=rt.id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 31),
        price_per_night=rt.base_price,
    ))
    pricing_rows.append(Pricing(
        room_type_id=rt.id,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 8, 31),
        price_per_night=round(rt.base_price * 1.35, 2),  # summer premium
    ))
    pricing_rows.append(Pricing(
        room_type_id=rt.id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        price_per_night=rt.base_price,
    ))

db.add_all(pricing_rows)
db.commit()

# --- 5. Guests -------------------------------------------------------------

guest_names = [
    ("Ava Thompson", "ava.thompson@example.com"),
    ("Liam Chen", "liam.chen@example.com"),
    ("Sofia Rossi", "sofia.rossi@example.com"),
    ("Noah Müller", "noah.mueller@example.com"),
    ("Isabella Silva", "isabella.silva@example.com"),
]

guests = [
    Guest(full_name=name, email=email, phone="+1-555-0100", loyalty_tier=random.choice(
        ["standard", "silver", "gold", "platinum"]))
    for name, email in guest_names
]
db.add_all(guests)
db.commit()

# --- 6. Sample bookings ------------------------------------------------

all_rooms = db.query(Room).all()
sample_bookings = []
for guest in guests:
    room = random.choice(all_rooms)
    check_in = date(2026, 9, random.randint(1, 20))
    nights = random.randint(2, 5)
    check_out = check_in + timedelta(days=nights)
    price_row = db.query(Pricing).filter(
        Pricing.room_type_id == room.room_type_id,
        Pricing.start_date <= check_in,
        Pricing.end_date >= check_in,
    ).first()
    nightly = price_row.price_per_night if price_row else room.room_type.base_price
    sample_bookings.append(Booking(
        guest_id=guest.id,
        room_id=room.id,
        check_in=check_in,
        check_out=check_out,
        status="confirmed",
        total_price=round(nightly * nights, 2),
    ))

db.add_all(sample_bookings)
db.commit()

# --- 7. Amenities ------------------------------------------------------

amenities = [
    Amenity(name="Rooftop Pool", category="Wellness",
            description="Heated infinity pool with skyline views.",
            hours="6:00 AM - 10:00 PM", location="Rooftop"),
    Amenity(name="Azure Spa", category="Wellness",
            description="Full-service spa offering massages, facials, and sauna access.",
            hours="9:00 AM - 8:00 PM", location="2nd Floor"),
    Amenity(name="The Lighthouse Restaurant", category="Dining",
            description="Fine dining restaurant specializing in coastal Mediterranean cuisine.",
            hours="6:00 PM - 11:00 PM", location="Ground Floor"),
    Amenity(name="Business Center", category="Business",
            description="Private meeting rooms, printing services, and high-speed WiFi.",
            hours="24 hours", location="1st Floor"),
    Amenity(name="Fitness Center", category="Wellness",
            description="Full gym with cardio equipment, free weights, and personal trainers on request.",
            hours="24 hours", location="3rd Floor"),
]
db.add_all(amenities)
db.commit()

# --- 8. FAQs -------------------------------------------------------------

faqs = [
    FAQ(question="What time is check-in and check-out?",
        answer="Check-in is at 3:00 PM and check-out is at 11:00 AM. Early check-in and late check-out "
               "may be available on request, subject to availability.",
        category="check-in"),
    FAQ(question="What is your cancellation policy?",
        answer="Reservations can be cancelled free of charge up to 48 hours before check-in. "
               "Cancellations within 48 hours are subject to a one-night charge.",
        category="cancellation"),
    FAQ(question="Are pets allowed?",
        answer="We welcome pets under 25 lbs for an additional fee of $50 per stay. "
               "Please notify us in advance so we can prepare a pet-friendly room.",
        category="pets"),
    FAQ(question="Is parking available?",
        answer="Valet parking is available for $35 per night. Self-parking is not offered at this location.",
        category="parking"),
    FAQ(question="Do you offer airport transfers?",
        answer="Yes, airport transfers can be arranged through the concierge for $60 each way. "
               "Please book at least 24 hours in advance.",
        category="transport"),
]
db.add_all(faqs)
db.commit()

db.close()

print("Database seeded successfully:")
print(f"  {len(room_types)} room types")
print(f"  {len(rooms)} rooms")
print(f"  {len(pricing_rows)} pricing rows")
print(f"  {len(guests)} guests")
print(f"  {len(sample_bookings)} bookings")
print(f"  {len(amenities)} amenities")
print(f"  {len(faqs)} FAQs")