CREATE TABLE airlines (
    code VARCHAR(2) PRIMARY KEY CHECK (code ~ '^[A-Z0-9]{2}$'),
    name VARCHAR(255) NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE airports (
    code VARCHAR(3) PRIMARY KEY CHECK (code ~ '^[A-Z]{3}$'),
    name VARCHAR(255) NOT NULL CHECK (length(trim(name)) > 0)
);

CREATE TABLE flights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_number VARCHAR(10) NOT NULL CHECK (flight_number ~ '^[A-Z0-9]{2}[0-9]{1,4}$'),
    airline_code VARCHAR(2) NOT NULL REFERENCES airlines(code),
    origin VARCHAR(3) NOT NULL REFERENCES airports(code),
    destination VARCHAR(3) NOT NULL REFERENCES airports(code),
    departure_at TIMESTAMPTZ NOT NULL,
    arrival_at TIMESTAMPTZ NOT NULL,
    total_seats INTEGER NOT NULL CHECK (total_seats > 0),
    available_seats INTEGER NOT NULL CHECK (available_seats >= 0 AND available_seats <= total_seats),
    price NUMERIC(12, 2) NOT NULL CHECK (price > 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')),
    CHECK (origin <> destination),
    CHECK (arrival_at > departure_at)
);

CREATE UNIQUE INDEX flights_number_day ON flights (flight_number, ((departure_at AT TIME ZONE 'UTC')::date));
CREATE INDEX flights_search ON flights (origin, destination, departure_at) WHERE status = 'SCHEDULED';

CREATE TABLE seat_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_id UUID NOT NULL REFERENCES flights(id),
    booking_id UUID NOT NULL UNIQUE,
    seat_count INTEGER NOT NULL CHECK (seat_count > 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX reservations_flight ON seat_reservations (flight_id);

CREATE TABLE reservation_cancellations (
    booking_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cache_epoch (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version BIGINT NOT NULL CHECK (version > 0)
);

INSERT INTO cache_epoch (id, version) VALUES (1, 1);
