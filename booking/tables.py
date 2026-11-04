from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Uuid


metadata = MetaData()
bookings = Table(
    "bookings", metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid),
    Column("flight_id", Uuid),
    Column("passenger_name", String),
    Column("passenger_email", String),
    Column("seat_count", Integer),
    Column("total_price", Numeric(18, 2)),
    Column("status", String),
    Column("created_at", DateTime(timezone=True)),
)
operations = Table(
    "booking_operations", metadata,
    Column("booking_id", Uuid, primary_key=True),
    Column("kind", String),
    Column("created_at", DateTime(timezone=True)),
)
