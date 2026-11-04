import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import grpc
import httpx
import pytest
from sqlalchemy import insert, text

import flights_pb2_grpc as rpc
from common.database import make_engine
from flight.cache import FlightCache
from flight.tables import airlines, airports, flights


@pytest.fixture(scope="session")
def flight_engine():
    engine = make_engine(os.environ["FLIGHT_DATABASE_URL"])
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def booking_engine():
    engine = make_engine(os.environ["BOOKING_DATABASE_URL"])
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def cache():
    instance = FlightCache(os.environ["REDIS_SENTINELS"])
    yield instance
    instance.client.close()


@pytest.fixture(autouse=True)
def clean_databases(flight_engine, booking_engine, cache):
    with booking_engine.begin() as connection:
        connection.execute(text("TRUNCATE booking_operations, bookings"))
    with flight_engine.begin() as connection:
        connection.execute(text("TRUNCATE reservation_cancellations, seat_reservations, flights, airlines, airports CASCADE"))
        connection.execute(text("UPDATE cache_epoch SET version = version + 1"))
    cache.client.flushdb()


@pytest.fixture
def make_flight(flight_engine):
    count = 0
    with flight_engine.begin() as connection:
        connection.execute(insert(airlines).values(code="SU", name="Aeroflot"))
        connection.execute(insert(airports), [
            {"code": "SVO", "name": "Sheremetyevo"}, {"code": "LED", "name": "Pulkovo"}, {"code": "VKO", "name": "Vnukovo"},
        ])

    def create(**overrides):
        nonlocal count
        count += 1
        departure = datetime(2027, 1, 10, 10, tzinfo=timezone.utc)
        values = {
            "id": uuid4(), "flight_number": f"SU{1000 + count}", "airline_code": "SU",
            "origin": "SVO", "destination": "LED", "departure_at": departure,
            "arrival_at": departure + timedelta(hours=2), "total_seats": 10, "available_seats": 10,
            "price": Decimal("5000.25"), "status": "SCHEDULED", **overrides,
        }
        with flight_engine.begin() as connection:
            connection.execute(insert(flights).values(**values))
            connection.execute(text("UPDATE cache_epoch SET version = version + 1"))
        return values

    return create


@pytest.fixture
def stub():
    with grpc.insecure_channel(os.environ["FLIGHT_GRPC_TARGET"], options=[("grpc.enable_retries", 0)]) as channel:
        yield rpc.FlightServiceStub(channel)


@pytest.fixture
def auth():
    return [("x-api-key", os.environ["GRPC_API_KEY"])]


@pytest.fixture
def api():
    with httpx.Client(base_url=os.environ["BOOKING_URL"], timeout=20) as client:
        yield client


@pytest.fixture
def booking_body(make_flight):
    flight = make_flight()
    return {
        "user_id": str(uuid4()), "flight_id": str(flight["id"]), "passenger_name": "Ivan Petrov",
        "passenger_email": "ivan@example.com", "seat_count": 2,
    }
