from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

import flights_pb2 as pb
from booking.tables import bookings, operations
from flight.tables import flights, reservations


def reserve(stub, auth, flight_id, booking_id=None, count=1):
    return stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=str(flight_id), booking_id=str(booking_id or uuid4()), seat_count=count), metadata=auth, timeout=5)


def available(engine, flight_id):
    with engine.connect() as connection:
        return connection.execute(select(flights.c.available_seats).where(flights.c.id == flight_id)).scalar_one()


@pytest.mark.parametrize("method,payload", [
    ("SearchFlights", pb.SearchFlightsRequest()),
    ("GetFlight", pb.GetFlightRequest()),
    ("ReserveSeats", pb.ReserveSeatsRequest()),
    ("ReleaseReservation", pb.ReleaseReservationRequest()),
    ("UpdateFlight", pb.UpdateFlightRequest()),
])
@pytest.mark.parametrize("metadata", [[], [("x-api-key", "wrong")]])
def test_all_grpc_methods_require_auth(stub, method, payload, metadata):
    with pytest.raises(grpc.RpcError) as caught:
        getattr(stub, method)(payload, metadata=metadata, timeout=3)
    assert caught.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_rest_booking_flow_and_price_snapshot(api, stub, auth, booking_body, flight_engine, booking_engine):
    flight_id = booking_body["flight_id"]
    response = api.post("/bookings", json=booking_body)
    assert response.status_code == 201, response.text
    booking = response.json()
    assert Decimal(booking["total_price"]) == Decimal("10000.50")
    assert booking["status"] == "CONFIRMED"
    assert api.get(f"/flights/{flight_id}").json()["available_seats"] == 8
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=flight_id, price="9000.00"), metadata=auth)
    assert api.get(f"/bookings/{booking['id']}").json()["total_price"] == booking["total_price"]
    assert len(api.get("/bookings", params={"user_id": booking_body["user_id"]}).json()) == 1
    assert api.get("/bookings", params={"user_id": str(uuid4())}).json() == []
    cancelled = api.post(f"/bookings/{booking['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert api.get(f"/flights/{flight_id}").json()["available_seats"] == 10
    assert api.post(f"/bookings/{booking['id']}/cancel").status_code == 409
    with booking_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(operations)).scalar_one() == 0
    with flight_engine.connect() as connection:
        assert connection.execute(select(reservations.c.status)).scalar_one() == "RELEASED"


def test_search_optional_date_and_status(api, make_flight):
    first = make_flight()
    next_day = first["departure_at"] + timedelta(days=1)
    make_flight(departure_at=next_day, arrival_at=next_day + timedelta(hours=2))
    make_flight(status="CANCELLED")
    make_flight(origin="VKO")
    query = {"origin": "SVO", "destination": "LED"}
    assert len(api.get("/flights", params=query).json()) == 2
    result = api.get("/flights", params={**query, "date": "2027-01-10"})
    assert result.status_code == 200
    assert [row["id"] for row in result.json()] == [str(first["id"])]
    assert api.get("/flights", params={**query, "date": "2027-01-12"}).json() == []


@pytest.mark.parametrize("path,status", [
    ("/flights", 422), ("/flights?origin=SV&destination=LED", 422),
    ("/flights?origin=SVO&destination=SVO", 400),
    ("/flights?origin=SVO&destination=LED&date=bad", 422),
    ("/flights/not-a-uuid", 422), ("/bookings", 422),
])
def test_invalid_rest_queries(api, path, status):
    assert api.get(path).status_code == status


@pytest.mark.parametrize("field,value", [
    ("seat_count", 0), ("seat_count", -1), ("seat_count", True), ("seat_count", 1.5),
    ("passenger_name", " "), ("passenger_email", "bad"), ("user_id", "bad"), ("flight_id", "bad"),
])
def test_invalid_booking_does_not_change_data(api, booking_body, field, value, flight_engine, booking_engine):
    assert api.post("/bookings", json={**booking_body, field: value}).status_code == 422
    with booking_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(bookings)).scalar_one() == 0
    with flight_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(reservations)).scalar_one() == 0


@pytest.mark.parametrize("resource", ["flights", "bookings"])
def test_missing_rest_resource(api, resource):
    assert api.get(f"/{resource}/{uuid4()}").status_code == 404


def test_no_booking_when_seats_unavailable(api, booking_body, booking_engine, flight_engine):
    response = api.post("/bookings", json={**booking_body, "seat_count": 11})
    assert response.status_code == 409
    assert response.json()["error_code"] == "INSUFFICIENT_SEATS"
    with booking_engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(bookings)).scalar_one() == 0
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 10


@pytest.mark.parametrize("status", ["DEPARTED", "CANCELLED", "COMPLETED"])
def test_cannot_reserve_non_scheduled(stub, auth, make_flight, status):
    flight = make_flight(status=status)
    with pytest.raises(grpc.RpcError) as caught:
        reserve(stub, auth, flight["id"])
    assert caught.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_reservation_idempotency_and_conflicts(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    first = reserve(stub, auth, flight["id"], booking_id, 2)
    assert reserve(stub, auth, flight["id"], booking_id, 2) == first
    assert available(flight_engine, flight["id"]) == 8
    for other_flight, count in [(flight["id"], 3), (uuid4(), 2)]:
        with pytest.raises(grpc.RpcError) as caught:
            reserve(stub, auth, other_flight, booking_id, count)
        assert caught.value.code() == grpc.StatusCode.ALREADY_EXISTS
    request = pb.ReleaseReservationRequest(booking_id=str(booking_id))
    released = stub.ReleaseReservation(request, metadata=auth)
    assert released.status == pb.RELEASED
    assert stub.ReleaseReservation(request, metadata=auth) == released
    assert available(flight_engine, flight["id"]) == 10
    with pytest.raises(grpc.RpcError) as caught:
        reserve(stub, auth, flight["id"], booking_id, 2)
    assert caught.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_last_seat_concurrent_requests(stub, auth, make_flight, flight_engine):
    flight = make_flight(total_seats=1, available_seats=1)

    def attempt(_):
        try:
            reserve(stub, auth, flight["id"])
            return grpc.StatusCode.OK
        except grpc.RpcError as error:
            return error.code()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count(grpc.StatusCode.OK) == 1
    assert outcomes.count(grpc.StatusCode.RESOURCE_EXHAUSTED) == 7
    assert available(flight_engine, flight["id"]) == 0


def test_same_booking_concurrent_reservations(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: reserve(stub, auth, flight["id"], booking_id, 2), range(8)))
    assert len({reply.id for reply in replies}) == 1
    assert available(flight_engine, flight["id"]) == 8


def test_concurrent_release_returns_seats_once(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    reservation = reserve(stub, auth, flight["id"], count=2)
    request = pb.ReleaseReservationRequest(booking_id=reservation.booking_id)
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: stub.ReleaseReservation(request, metadata=auth), range(8)))
    assert all(reply.status == pb.RELEASED for reply in replies)
    assert available(flight_engine, flight["id"]) == 10


def test_release_before_delayed_reserve_is_fenced(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    booking_id = uuid4()
    with pytest.raises(grpc.RpcError) as caught:
        stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=str(booking_id)), metadata=auth)
    assert caught.value.code() == grpc.StatusCode.NOT_FOUND
    with pytest.raises(grpc.RpcError) as caught:
        reserve(stub, auth, flight["id"], booking_id)
    assert caught.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert available(flight_engine, flight["id"]) == 10


def test_cache_ttl_invalidation_and_old_value_after_failover(stub, auth, make_flight, cache):
    flight = make_flight()
    flight_id = str(flight["id"])
    request = pb.GetFlightRequest(flight_id=flight_id)
    search = pb.SearchFlightsRequest(origin="SVO", destination="LED")
    stamp = Timestamp()
    stamp.FromDatetime(datetime(2027, 1, 10, tzinfo=timezone.utc))
    dated_search = pb.SearchFlightsRequest(origin="SVO", destination="LED", departure_date=stamp)
    for _ in range(2):
        assert stub.GetFlight(request, metadata=auth).available_seats == 10
        assert len(stub.SearchFlights(search, metadata=auth).flights) == 1
        assert len(stub.SearchFlights(dated_search, metadata=auth).flights) == 1
    keys = [f"flight:{flight_id}", "search:SVO:LED:all", "search:SVO:LED:2027-01-10"]
    old_values = {key: cache.client.get(key) for key in keys}
    assert all(0 < cache.client.ttl(key) <= 600 for key in keys)
    reservation = reserve(stub, auth, flight_id, count=2)
    assert all(cache.client.get(key) is None for key in keys)
    for key, value in old_values.items():
        cache.client.set(key, value, ex=600)
    assert stub.GetFlight(request, metadata=auth).available_seats == 8
    assert stub.SearchFlights(search, metadata=auth).flights[0].available_seats == 8
    stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=reservation.booking_id), metadata=auth)
    assert stub.GetFlight(request, metadata=auth).available_seats == 10
    stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=flight_id, price="7500.50", status=pb.CANCELLED), metadata=auth)
    assert stub.GetFlight(request, metadata=auth).price == "7500.50"
    assert stub.SearchFlights(search, metadata=auth).flights == []


def test_capacity_update_preserves_reserved_seats(stub, auth, make_flight, flight_engine):
    flight = make_flight()
    reserve(stub, auth, flight["id"], count=4)
    response = stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=str(flight["id"]), total_seats=12), metadata=auth)
    assert response.total_seats == 12 and response.available_seats == 8
    with pytest.raises(grpc.RpcError) as caught:
        stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=str(flight["id"]), total_seats=3), metadata=auth)
    assert caught.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert available(flight_engine, flight["id"]) == 8


@pytest.mark.parametrize("price", ["0", "-1", "1.001", "NaN", "Infinity", "abc"])
def test_invalid_update_price(stub, auth, make_flight, price):
    flight = make_flight()
    with pytest.raises(grpc.RpcError) as caught:
        stub.UpdateFlight(pb.UpdateFlightRequest(flight_id=str(flight["id"]), price=price), metadata=auth)
    assert caught.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.parametrize("assignment", ["available_seats=-1", "available_seats=11", "total_seats=0", "price=0", "origin=destination", "arrival_at=departure_at"])
def test_database_constraints(flight_engine, make_flight, assignment):
    flight = make_flight()
    with pytest.raises(IntegrityError), flight_engine.begin() as connection:
        connection.execute(text(f"UPDATE flights SET {assignment} WHERE id = :id"), {"id": flight["id"]})


def test_unique_flight_number_and_utc_date(make_flight):
    flight = make_flight()
    with pytest.raises(IntegrityError):
        make_flight(flight_number=flight["flight_number"], departure_at=flight["departure_at"] + timedelta(hours=1))
    make_flight(flight_number=flight["flight_number"], departure_at=flight["departure_at"] + timedelta(days=1), arrival_at=flight["arrival_at"] + timedelta(days=1))


def test_rest_contract_has_only_homework_routes(api):
    assert set(api.get("/openapi.json").json()["paths"]) == {
        "/flights", "/flights/{flight_id}", "/bookings", "/bookings/{booking_id}", "/bookings/{booking_id}/cancel",
    }
