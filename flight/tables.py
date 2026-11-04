from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, Numeric, String, Table, Uuid


metadata = MetaData()
airlines = Table("airlines", metadata, Column("code", String, primary_key=True), Column("name", String))
airports = Table("airports", metadata, Column("code", String, primary_key=True), Column("name", String))
flights = Table(
    "flights", metadata,
    Column("id", Uuid, primary_key=True),
    Column("flight_number", String),
    Column("airline_code", String, ForeignKey("airlines.code")),
    Column("origin", String, ForeignKey("airports.code")),
    Column("destination", String, ForeignKey("airports.code")),
    Column("departure_at", DateTime(timezone=True)),
    Column("arrival_at", DateTime(timezone=True)),
    Column("total_seats", Integer),
    Column("available_seats", Integer),
    Column("price", Numeric(12, 2)),
    Column("status", String),
)
reservations = Table(
    "seat_reservations", metadata,
    Column("id", Uuid, primary_key=True),
    Column("flight_id", Uuid, ForeignKey("flights.id")),
    Column("booking_id", Uuid, unique=True),
    Column("seat_count", Integer),
    Column("status", String),
    Column("created_at", DateTime(timezone=True)),
)
cancellations = Table(
    "reservation_cancellations", metadata,
    Column("booking_id", Uuid, primary_key=True),
    Column("created_at", DateTime(timezone=True)),
)
cache_epoch = Table(
    "cache_epoch", metadata, Column("id", Integer, primary_key=True), Column("version", Integer)
)
