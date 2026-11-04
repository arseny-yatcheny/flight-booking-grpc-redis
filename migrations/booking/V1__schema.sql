CREATE TABLE bookings (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    flight_id UUID NOT NULL,
    passenger_name VARCHAR(255) NOT NULL CHECK (length(trim(passenger_name)) > 0),
    passenger_email VARCHAR(254) NOT NULL,
    seat_count INTEGER NOT NULL CHECK (seat_count > 0),
    total_price NUMERIC(18, 2) NOT NULL CHECK (total_price > 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX bookings_user ON bookings (user_id, created_at);

CREATE TABLE booking_operations (
    booking_id UUID PRIMARY KEY,
    kind VARCHAR(6) NOT NULL CHECK (kind IN ('CREATE', 'CANCEL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
