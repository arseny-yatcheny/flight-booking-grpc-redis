import re
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import flights_pb2 as pb
import flights_pb2_grpc as rpc
from common.database import lock_booking
from flight.errors import FlightError
from flight.tables import airlines, cache_epoch, cancellations, flights, reservations


def parse_id(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "Expected a UUID")


def timestamp(value):
    result = Timestamp()
    result.FromDatetime(value)
    return result


def flight_message(row):
    return pb.Flight(
        id=str(row["id"]), flight_number=row["flight_number"], airline_code=row["airline_code"],
        airline_name=row["airline_name"], origin=row["origin"], destination=row["destination"],
        departure_at=timestamp(row["departure_at"]), arrival_at=timestamp(row["arrival_at"]),
        total_seats=row["total_seats"], available_seats=row["available_seats"],
        price=f"{row['price']:.2f}", status=pb.FlightStatus.Value(row["status"]),
    )


def reservation_message(row):
    return pb.SeatReservation(
        id=str(row["id"]), flight_id=str(row["flight_id"]), booking_id=str(row["booking_id"]),
        seat_count=row["seat_count"], status=pb.ReservationStatus.Value(row["status"]),
        created_at=timestamp(row["created_at"]),
    )


class FlightService(rpc.FlightServiceServicer):
    def __init__(self, engine, cache):
        self.engine = engine
        self.cache = cache

    def query_flights(self):
        return select(flights, airlines.c.name.label("airline_name")).join(airlines)

    def changed(self, connection):
        connection.execute(update(cache_epoch).where(cache_epoch.c.id == 1).values(version=cache_epoch.c.version + 1))

    def GetFlight(self, request, context):
        flight_id = parse_id(request.flight_id)
        key = f"flight:{flight_id}"
        with self.engine.connect() as connection:
            version = connection.execute(select(cache_epoch.c.version).where(cache_epoch.c.id == 1)).scalar_one()
            cached = self.cache.get(key, version, pb.Flight)
            if cached is not None:
                return cached
            row = connection.execute(self.query_flights().where(flights.c.id == flight_id)).mappings().first()
            if row is None:
                raise FlightError(grpc.StatusCode.NOT_FOUND, "Flight not found")
            result = flight_message(row)
        self.cache.put(key, version, result)
        return result

    def SearchFlights(self, request, context):
        if not re.fullmatch(r"[A-Z]{3}", request.origin) or not re.fullmatch(r"[A-Z]{3}", request.destination):
            raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "Airports must be three-letter IATA codes")
        if request.origin == request.destination:
            raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "Origin and destination must differ")
        query = self.query_flights().where(
            flights.c.origin == request.origin, flights.c.destination == request.destination, flights.c.status == "SCHEDULED"
        )
        date_key = "all"
        if request.HasField("departure_date"):
            try:
                day = request.departure_date.ToDatetime(tzinfo=timezone.utc)
                if day.hour or day.minute or day.second or request.departure_date.nanos:
                    raise ValueError()
                next_day = day + timedelta(days=1)
            except (ValueError, OverflowError):
                raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "departure_date must be midnight UTC")
            date_key = day.date().isoformat()
            query = query.where(flights.c.departure_at >= day, flights.c.departure_at < next_day)
        key = f"search:{request.origin}:{request.destination}:{date_key}"
        with self.engine.connect() as connection:
            version = connection.execute(select(cache_epoch.c.version).where(cache_epoch.c.id == 1)).scalar_one()
            cached = self.cache.get(key, version, pb.SearchFlightsResponse)
            if cached is not None:
                return cached
            rows = connection.execute(query.order_by(flights.c.departure_at, flights.c.id)).mappings()
            result = pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])
        self.cache.put(key, version, result)
        return result

    def ReserveSeats(self, request, context):
        flight_id = parse_id(request.flight_id)
        booking_id = parse_id(request.booking_id)
        if request.seat_count <= 0:
            raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be positive")
        with self.engine.begin() as connection:
            lock_booking(connection, booking_id)
            cancelled = connection.execute(select(cancellations.c.booking_id).where(cancellations.c.booking_id == booking_id)).first()
            if cancelled:
                raise FlightError(grpc.StatusCode.FAILED_PRECONDITION, "Reservation request was cancelled")
            existing = connection.execute(select(reservations).where(reservations.c.booking_id == booking_id)).mappings().first()
            if existing:
                if existing["flight_id"] != flight_id or existing["seat_count"] != request.seat_count:
                    raise FlightError(grpc.StatusCode.ALREADY_EXISTS, "booking_id was used with different parameters")
                if existing["status"] != "ACTIVE":
                    raise FlightError(grpc.StatusCode.FAILED_PRECONDITION, "Reservation is no longer active")
                return reservation_message(existing)
            flight = connection.execute(select(flights).where(flights.c.id == flight_id).with_for_update()).mappings().first()
            if flight is None:
                raise FlightError(grpc.StatusCode.NOT_FOUND, "Flight not found")
            if flight["status"] != "SCHEDULED":
                raise FlightError(grpc.StatusCode.FAILED_PRECONDITION, "Flight is not scheduled")
            if flight["available_seats"] < request.seat_count:
                raise FlightError(grpc.StatusCode.RESOURCE_EXHAUSTED, "Not enough available seats")
            connection.execute(update(flights).where(flights.c.id == flight_id).values(available_seats=flight["available_seats"] - request.seat_count))
            row = connection.execute(insert(reservations).values(
                id=uuid4(), flight_id=flight_id, booking_id=booking_id, seat_count=request.seat_count, status="ACTIVE"
            ).returning(reservations)).mappings().one()
            self.changed(connection)
        self.cache.invalidate(flight)
        return reservation_message(row)

    def ReleaseReservation(self, request, context):
        booking_id = parse_id(request.booking_id)
        with self.engine.begin() as connection:
            lock_booking(connection, booking_id)
            row = connection.execute(select(reservations).where(reservations.c.booking_id == booking_id)).mappings().first()
            if row is None:
                connection.execute(pg_insert(cancellations).values(booking_id=booking_id).on_conflict_do_nothing())
            elif row["status"] != "ACTIVE":
                return reservation_message(row)
            else:
                flight = connection.execute(select(flights).where(flights.c.id == row["flight_id"]).with_for_update()).mappings().one()
                connection.execute(update(flights).where(flights.c.id == row["flight_id"]).values(
                    available_seats=flight["available_seats"] + row["seat_count"]
                ))
                row = connection.execute(update(reservations).where(reservations.c.id == row["id"]).values(status="RELEASED").returning(reservations)).mappings().one()
                self.changed(connection)
        if row is None:
            raise FlightError(grpc.StatusCode.NOT_FOUND, "Reservation not found; delayed creation has been blocked")
        self.cache.invalidate(flight)
        return reservation_message(row)

    def UpdateFlight(self, request, context):
        flight_id = parse_id(request.flight_id)
        values = {}
        if request.HasField("price"):
            try:
                price = Decimal(request.price)
                if not price.is_finite() or not Decimal("0.01") <= price <= Decimal("9999999999.99") or price != price.quantize(Decimal("0.01")):
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "price must be a positive decimal with at most two fractional digits")
            values["price"] = price
        if request.HasField("status"):
            if request.status not in (pb.SCHEDULED, pb.DEPARTED, pb.CANCELLED, pb.COMPLETED):
                raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "Unknown flight status")
            values["status"] = pb.FlightStatus.Name(request.status)
        if request.HasField("total_seats") and request.total_seats <= 0:
            raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "total_seats must be positive")
        if not values and not request.HasField("total_seats"):
            raise FlightError(grpc.StatusCode.INVALID_ARGUMENT, "No fields to update")
        with self.engine.begin() as connection:
            flight = connection.execute(select(flights).where(flights.c.id == flight_id).with_for_update()).mappings().first()
            if flight is None:
                raise FlightError(grpc.StatusCode.NOT_FOUND, "Flight not found")
            if request.HasField("total_seats"):
                available = flight["available_seats"] + request.total_seats - flight["total_seats"]
                if available < 0:
                    raise FlightError(grpc.StatusCode.FAILED_PRECONDITION, "Capacity cannot be lower than reserved seats")
                values.update(total_seats=request.total_seats, available_seats=available)
            connection.execute(update(flights).where(flights.c.id == flight_id).values(**values))
            self.changed(connection)
            result = flight_message(connection.execute(self.query_flights().where(flights.c.id == flight_id)).mappings().one())
        self.cache.invalidate(flight)
        return result
