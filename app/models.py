"""
SQLAlchemy models for Blue Horizon.

Tables:
    amenities    - category, name, price, duration , .....
    amanity_usage - 
    guests       - guest profiles
    room_types   - categories of room (suite, deluxe, standard...)
    rooms        - individual physical rooms
    pricing      - date-ranged pricing per room_type
    bookings     - reservations
    
    faqs         - policy Q&A pairs
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, Date, DateTime,
    ForeignKey, CheckConstraint, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Guest(Base):
    __tablename__ = "guests"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    phone = Column(String(30))
    loyalty_tier = Column(String(20), default="standard")  # standard, silver, gold, platinum
    created_at = Column(DateTime, default=datetime.utcnow)

    bookings = relationship("Booking", back_populates="guest")


class RoomType(Base):
    __tablename__ = "room_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)   # e.g. "Deluxe King", "Ocean Suite"
    description = Column(Text)
    max_occupancy = Column(Integer, nullable=False)
    base_price = Column(Float, nullable=False)               # fallback if no pricing row matches
    amenities_summary = Column(Text)                          # short freeform list, e.g. "WiFi, Minibar, Balcony"

    rooms = relationship("Room", back_populates="room_type")
    pricing = relationship("Pricing", back_populates="room_type")


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    room_number = Column(String(10), unique=True, nullable=False)
    floor = Column(Integer, nullable=False)
    room_type_id = Column(Integer, ForeignKey("room_types.id"), nullable=False)
    is_active = Column(Boolean, default=True)  # false = out of service / under renovation

    room_type = relationship("RoomType", back_populates="rooms")
    bookings = relationship("Booking", back_populates="room")


class Pricing(Base):
    __tablename__ = "pricing"

    id = Column(Integer, primary_key=True)
    room_type_id = Column(Integer, ForeignKey("room_types.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    price_per_night = Column(Float, nullable=False)

    room_type = relationship("RoomType", back_populates="pricing")

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="valid_date_range"),
    )


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True)
    guest_id = Column(Integer, ForeignKey("guests.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    check_in = Column(Date, nullable=False)
    check_out = Column(Date, nullable=False)
    status = Column(String(20), default="confirmed")  # confirmed, cancelled, completed, pending
    total_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    guest = relationship("Guest", back_populates="bookings")
    room = relationship("Room", back_populates="bookings")

    __table_args__ = (
        CheckConstraint("check_out > check_in", name="valid_stay_range"),
    )


class Amenity(Base):
    __tablename__ = "amenities"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)          # e.g. "Rooftop Pool"
    category = Column(String(50))                         # e.g. "Wellness", "Dining", "Business"
    description = Column(Text)
    hours = Column(String(100))                           # e.g. "6:00 AM - 10:00 PM"
    location = Column(String(100))                        # e.g. "5th Floor"


class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    category = Column(String(50))  # e.g. "check-in", "cancellation", "pets", "parking"