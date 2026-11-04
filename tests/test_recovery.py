import os
from contextlib import contextmanager
from uuid import uuid4

import grpc
import pytest
from sqlalchemy import func, insert, select, text

import flights_pb2 as pb
from booking.client import FlightClient
from booking.service import BookingService, booking_lock
from booking.tables import bookings, operations
from flight.tables import flights, reservations


@contextmanager
def reject_mutation(engine, table, event):
    with engine.begin() as connection:
        connection.execute(text("CREATE FUNCTION test_reject_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test transaction rollback'; END $$"))
        connection.execute(text(f"CREATE TRIGGER test_reject_write BEFORE {event} ON {table} FOR EACH ROW EXECUTE FUNCTION test_reject_write()"))
    try:
        yield
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP TRIGGER test_reject_write ON {table}"))
            connection.execute(text("DROP FUNCTION test_reject_write()"))


@pytest.fixture
def service(booking_engine):
    client = FlightClient(os.environ["FLIGHT_GRPC_TARGET"], os.environ["GRPC_API_KEY"])
    yield BookingService(booking_engine, client)
    client.close()


def test_reservation_insert_failure_rolls_back_stock(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    with reject_mutation(flight_engine, "seat_reservations", "INSERT"):
        with pytest.raises(grpc.RpcError) as caught:
            stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=str(flight["id"]), booking_id=str(uuid4()), seat_count=2), metadata=auth)
        assert caught.value.code() == grpc.StatusCode.UNAVAILABLE
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10
        assert connection.execute(select(func.count()).select_from(reservations)).scalar_one() == 0


def test_release_failure_rolls_back_returned_stock(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    booking_id = str(uuid4())
    stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=str(flight["id"]), booking_id=booking_id, seat_count=2), metadata=auth)
    with reject_mutation(flight_engine, "seat_reservations", "UPDATE"):
        with pytest.raises(grpc.RpcError):
            stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=booking_id), metadata=auth)
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 8
        assert connection.execute(select(reservations.c.status)).scalar_one() == "ACTIVE"


def test_booking_insert_failure_compensates_reservation(api, booking_body, booking_engine, flight_engine):
    with reject_mutation(booking_engine, "bookings", "INSERT"):
        response = api.post("/bookings", json=booking_body)
    assert response.status_code == 503, response.text
    with booking_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(bookings)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(operations)).scalar_one() == 0
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10
        assert connection.execute(select(reservations.c.status)).scalar_one() == "RELEASED"


def test_recovery_after_crash_between_reserve_and_booking(service, make_flight, booking_engine, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    with booking_engine.begin() as connection:
        connection.execute(insert(operations).values(booking_id=booking_id, kind="CREATE"))
    service.client.reserve(flight["id"], booking_id, 2)
    service.recover()
    service.recover()
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10
        assert connection.execute(select(reservations.c.status)).scalar_one() == "RELEASED"
    with booking_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(operations)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(bookings)).scalar_one() == 0


def test_recovery_does_not_interrupt_in_progress_request(service, make_flight, booking_engine, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    with booking_lock(booking_engine, booking_id) as connection:
        with connection.begin():
            connection.execute(insert(operations).values(booking_id=booking_id, kind="CREATE"))
        service.client.reserve(flight["id"], booking_id, 2)
        service.recover()
        with flight_engine.connect() as inspection:
            assert inspection.execute(select(flights.c.available_seats)).scalar_one() == 8
    service.recover()
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10


def test_recovery_completes_cancel_after_local_database_failure(api, booking_body, booking_engine, flight_engine, service):
    booking = api.post("/bookings", json=booking_body).json()
    with reject_mutation(booking_engine, "bookings", "UPDATE"):
        response = api.post(f"/bookings/{booking['id']}/cancel")
    assert response.status_code == 503
    assert api.get(f"/bookings/{booking['id']}").json()["status"] == "CONFIRMED"
    service.recover()
    assert api.get(f"/bookings/{booking['id']}").json()["status"] == "CANCELLED"
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10


def test_recovery_blocks_reservation_that_never_arrived(service, make_flight, booking_engine, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    with booking_engine.begin() as connection:
        connection.execute(insert(operations).values(booking_id=booking_id, kind="CREATE"))
    service.recover()
    with pytest.raises(grpc.RpcError) as caught:
        service.client.reserve(flight["id"], booking_id, 2)
    assert caught.value.code() == grpc.StatusCode.FAILED_PRECONDITION
