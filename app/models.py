from sqlalchemy import (
    Column, Integer, String, Text, Float, Date, DateTime,
    ForeignKey, Boolean, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------------------------------------------------------------------------
# Core: customers, rooms, bookings
# ---------------------------------------------------------------------------

class Customers(Base):
    __tablename__ = "customers"

    customer_id = Column(Integer, primary_key=True)
    first_name = Column(String(80))
    last_name = Column(String(80))
    email = Column(String(120))
    phone = Column(String(30))
    address = Column(Text)
    preferences = Column(Text)
    nationality = Column(String(80))
    language = Column(String(40))
    loyalty_tier = Column(String(20))

    bookings = relationship("RoomBookings", back_populates="customer")


class Rooms(Base):
    __tablename__ = "rooms"

    room_id = Column(String(10), primary_key=True)
    room_number = Column(Integer, unique=True, nullable=False, index=True)
    floor = Column(Integer)
    type = Column(String(50))
    square_feet = Column(Integer)
    basic_amenities = Column(Text)
    additional_amenities = Column(Text)
    max_occupancy = Column(Integer)
    bed_type = Column(String(50))
    view_type = Column(String(50))
    accessibility = Column(String(100))
    status = Column(String(30))
    last_renovation = Column(Date)
    base_rate = Column(Integer)
    max_rate = Column(Integer)

    availability = relationship("RoomAvailability", back_populates="room")


class RoomBookings(Base):
    __tablename__ = "room_bookings"

    booking_id = Column(String(10), primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    room_number = Column(Integer, ForeignKey("rooms.room_number"))
    room_type = Column(String(50))
    check_in = Column(DateTime)
    check_out = Column(DateTime)
    duration_days = Column(Integer)
    num_adults = Column(Integer)
    num_children = Column(Integer)
    loyalty_tier = Column(String(20))
    special_amenities = Column(Text)
    special_requests = Column(Text)
    booking_status = Column(String(30))
    payment_method = Column(String(30))
    total_amount = Column(Float)
    points_earned = Column(Integer)

    customer = relationship("Customers", back_populates="bookings")


class RoomAvailability(Base):
    __tablename__ = "room_availability"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(10), ForeignKey("rooms.room_id"))
    room_number = Column(Integer)
    date = Column(Date, nullable=False, index=True)
    status = Column(String(30))
    price = Column(Float)
    max_occupancy = Column(Integer)

    room = relationship("Rooms", back_populates="availability")

    __table_args__ = (
        UniqueConstraint("room_id", "date", name="uq_room_date"),
    )


# ---------------------------------------------------------------------------
# Amenities & usage
# ---------------------------------------------------------------------------

class Amenities(Base):
    __tablename__ = "amenities"

    amenity_id = Column(String(10), primary_key=True)
    category = Column(String(80))
    name = Column(String(80))
    price = Column(Integer)
    duration = Column(Integer)
    description = Column(Text)
    availability = Column(String(40))
    location = Column(Text)
    booking_required = Column(Boolean)
    min_notice_hours = Column(Integer)


class AmenityUsage(Base):
    __tablename__ = "amenity_usage"

    usage_id = Column(String(10), primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    amenity_type = Column(String(30))
    service_name = Column(String(80))
    usage_date = Column(Date)
    duration_minutes = Column(Integer)
    cost = Column(Float)
    payment_method = Column(String(30))
    staff_id = Column(String(10), ForeignKey("staff.staff_id"))
    staff_name = Column(Text)
    satisfaction_rating = Column(Integer)

    customer = relationship("Customers")
    staff = relationship("Staff")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventSpaces(Base):
    __tablename__ = "event_spaces"

    space_id = Column(String(10), primary_key=True)
    name = Column(String(30))
    location = Column(String(30))
    capacity = Column(Integer)
    square_feet = Column(Integer)
    price_per_hour = Column(Integer)
    features = Column(Text) 
    layout_options = Column(Text)
    min_booking_hours = Column(Integer)
    availability = Column(String(30))
    catering_available = Column(Boolean)
    setup_time = Column(Integer)
    cleanup_time = Column(Integer)
    accessibility = Column(String(40))


class EventBookings(Base):
    __tablename__ = "event_bookings"

    event_booking_id = Column(String(10), primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    event_type = Column(String(30))
    space_id = Column(String(10), ForeignKey("event_spaces.space_id"))
    event_date = Column(Date)
    duration_hours = Column(Integer)
    attendees = Column(Integer)
    setup_requirements = Column(Text)
    additional_setup_notes = Column(Text)
    setup_start_time = Column(DateTime)
    setup_completion_time = Column(DateTime)

    customer = relationship("Customers")
    space = relationship("EventSpaces")


class EventTracking(Base):
    __tablename__ = "event_tracking"

    event_id = Column(String(10), primary_key=True)
    booking_id = Column(String(10))
    event_type = Column(String(30))
    event_name = Column(String(30))
    timestamp = Column(DateTime)
    details = Column(Text)
    staff_id = Column(String(10), ForeignKey("staff.staff_id"))
    status = Column(String(30))

    staff = relationship("Staff")


# ---------------------------------------------------------------------------
# Restaurant
# ---------------------------------------------------------------------------

class RestaurantBookings(Base):
    __tablename__ = "restaurant_bookings"

    booking_id = Column(String(10), primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    restaurant = Column(String(40))
    booking_date = Column(DateTime)
    time_slot = Column(String(10))
    party_size = Column(Integer)
    special_requests = Column(Text)
    dietary_requirements = Column(String(40))

    customer = relationship("Customers")


# ---------------------------------------------------------------------------
# Services & appointments
# ---------------------------------------------------------------------------

class Services(Base):
    __tablename__ = "services"

    service_id = Column(String(10), primary_key=True)
    # Widened from String(20): "Wellness Consultation" alone is 22 chars.
    service_type = Column(String(50))
    name = Column(String(50))
    description = Column(Text)
    duration_minutes = Column(Integer)
    price = Column(Float)
    department = Column(String(50))
    booking_required = Column(Boolean)
    min_notice_hours = Column(Integer)


class ServiceAppointments(Base):
    __tablename__ = "service_appointments"

    appointment_id = Column(String(10), primary_key=True)
    booking_id = Column(String(10))
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    service_type = Column(String(50))
    department = Column(String(20))
    appointment_date = Column(DateTime)
    appointment_end = Column(DateTime)
    duration_minutes = Column(Integer)
    description = Column(Text)
    cost = Column(Float)
    status = Column(String(30))
    staff_id = Column(String(10), ForeignKey("staff.staff_id"))
    staff_name = Column(String(50))

    customer = relationship("Customers")
    staff = relationship("Staff")


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

class Staff(Base):
    __tablename__ = "staff"

    staff_id = Column(String(10), primary_key=True)
    first_name = Column(String(50))
    last_name = Column(String(50))
    email = Column(Text)
    phone = Column(String(30))
    hire_date = Column(Date)
    department = Column(String(50))
    schedule = Column(String(20))
    certifications = Column(Text)
    position = Column(String(50))


class StaffSchedules(Base):
    __tablename__ = "staff_schedules"

    schedule_id = Column(Integer, primary_key=True, autoincrement=True)
    staff_id = Column(String(10), ForeignKey("staff.staff_id"), nullable=False)
    date = Column(Date, nullable=False)
    shift = Column(String(20))
    department = Column(String(50))

    staff = relationship("Staff")


# ---------------------------------------------------------------------------
# Feedback & payments
# ---------------------------------------------------------------------------

class Feedback(Base):
    __tablename__ = "feedback"

    feedback_id = Column(String(10), primary_key=True)
    booking_id = Column(String(10), ForeignKey("room_bookings.booking_id"))
    customer_id = Column(Integer, ForeignKey("customers.customer_id"))
    rating = Column(Integer)
    feedback_text = Column(Text)
    feedback_date = Column(DateTime)
    sentiment = Column(String(20))
    category = Column(String(20))
    subcategory = Column(String(20))
    feedback_source = Column(String(20))
    language = Column(String(20))
    is_verified_stay = Column(Boolean)
    helpful_votes = Column(Integer)
    satisfaction_scores = Column(Text)
    tags = Column(Text)
    response_required = Column(Boolean)
    response_text = Column(Text)
    staff_response = Column(Text)
    response_date = Column(DateTime)
    response_time_hours = Column(Float)
    resolved = Column(Boolean)
    status = Column(String(30))

    booking = relationship("RoomBookings")
    customer = relationship("Customers")


class Payments(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_id = Column(String(10), index=True) 
    booking_id = Column(String(10), ForeignKey("room_bookings.booking_id"))
    amount = Column(Float)
    payment_method = Column(String(30))
    payment_provider = Column(String(30))
    status = Column(String(20))
    timestamp = Column(DateTime)
    processing_fee = Column(Float)
    total_charged = Column(Float)

    booking = relationship("RoomBookings")


# ---------------------------------------------------------------------------
# Promotions & recommendations (standalone reference tables, no FKs needed)
# ---------------------------------------------------------------------------

class Promotions(Base):
    __tablename__ = "promotions"

    promotion_id = Column(String(10), primary_key=True)
    name = Column(String(30))
    description = Column(Text)
    discount_type = Column(String(30))
    discount_value = Column(Integer)
    min_stay = Column(Integer)
    applicable_room_types = Column(String(100))
    start_date = Column(Date)
    end_date = Column(Date)
    blackout_dates = Column(Text)
    terms_conditions = Column(Text)
    booking_code = Column(String(10))
    status = Column(String(20))


class RecommendationsKnowledgeBase(Base):
    __tablename__ = "recommendations_knowledge_base"

    recommendation_id = Column(String(10), primary_key=True)
    category = Column(String(30))
    name = Column(String(100))
    description = Column(Text)
    address = Column(Text)
    distance_km = Column(Float)
    price_range = Column(String(30))
    rating = Column(Float)
    review_count = Column(Integer)
    booking_required = Column(Boolean)
    seasonal = Column(Boolean)
    tags = Column(Text)
    keywords = Column(Text)
    last_verified = Column(Date)
 

class FAQKnowledgeBase(Base):
    __tablename__ = "faq_knowledge_base"

    faq_id = Column(String(10), primary_key=True)
    category = Column(String(30))
    subcategory = Column(String(30))
    question = Column(Text)
    answer = Column(Text)
    keywords = Column(Text)
    last_updated = Column(Date)
    helpful_votes = Column(Integer)
    views = Column(Integer)