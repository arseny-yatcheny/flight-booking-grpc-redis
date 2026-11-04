import os
import threading
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, time, timezone
from uuid import UUID

import grpc
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy.exc import SQLAlchemyError

import flights_pb2 as pb
from booking.client import FlightClient
from booking.schemas import BookingCreate, BookingResponse, ErrorResponse, FlightResponse
from booking.service import BookingError, BookingService
from common.database import make_engine
from common.logs import configure_logging, log


def flight_json(flight):
    return {
        "id": flight.id, "flight_number": flight.flight_number, "airline_code": flight.airline_code,
        "airline_name": flight.airline_name, "origin": flight.origin, "destination": flight.destination,
        "departure_at": flight.departure_at.ToDatetime(tzinfo=timezone.utc),
        "arrival_at": flight.arrival_at.ToDatetime(tzinfo=timezone.utc),
        "total_seats": flight.total_seats, "available_seats": flight.available_seats,
        "price": flight.price, "status": pb.FlightStatus.Name(flight.status),
    }


def create_app(engine=None, client=None, recover=True):
    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        app.state.engine = engine or make_engine(os.environ["DATABASE_URL"])
        app.state.client = client or FlightClient.from_environment()
        app.state.service = BookingService(app.state.engine, app.state.client)
        stopped = threading.Event()

        def worker():
            while not stopped.wait(float(os.getenv("RECOVERY_INTERVAL_SECONDS", "2"))):
                try:
                    app.state.service.recover()
                except SQLAlchemyError as error:
                    log("recovery_database_unavailable", error=type(error).__name__)

        thread = threading.Thread(target=worker, name="booking-recovery", daemon=True)
        if recover:
            thread.start()
        yield
        stopped.set()
        if recover:
            thread.join(timeout=8)
        if client is None:
            app.state.client.close()
        if engine is None:
            app.state.engine.dispose()

    app = FastAPI(
        title="Flight Booking", version="1.0.0", lifespan=lifespan, redoc_url=None,
        responses={code: {"model": ErrorResponse} for code in (400, 404, 409, 422, 503)},
    )

    @app.exception_handler(BookingError)
    async def booking_error(request, error):
        return JSONResponse(status_code=error.status, content={"error_code": error.code, "message": error.message})

    @app.exception_handler(grpc.RpcError)
    async def grpc_error(request, error):
        mappings = {
            grpc.StatusCode.INVALID_ARGUMENT: (400, "INVALID_ARGUMENT"),
            grpc.StatusCode.NOT_FOUND: (404, "FLIGHT_NOT_FOUND"),
            grpc.StatusCode.RESOURCE_EXHAUSTED: (409, "INSUFFICIENT_SEATS"),
            grpc.StatusCode.FAILED_PRECONDITION: (409, "INVALID_FLIGHT_STATE"),
            grpc.StatusCode.ALREADY_EXISTS: (409, "RESERVATION_CONFLICT"),
        }
        status, code = mappings.get(error.code(), (503, "FLIGHT_SERVICE_UNAVAILABLE"))
        message = error.details() if status != 503 else "Flight Service is temporarily unavailable; try again later"
        return JSONResponse(status_code=status, content={"error_code": code, "message": message})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        log("booking_database_error", error=type(error).__name__)
        return JSONResponse(status_code=503, content={"error_code": "DATABASE_UNAVAILABLE", "message": "Booking database is temporarily unavailable"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = [{"field": ".".join(map(str, item["loc"])), "message": item["msg"]} for item in error.errors()]
        return JSONResponse(status_code=422, content={"error_code": "VALIDATION_ERROR", "message": "Invalid request", "details": details})

    @app.get("/flights", response_model=list[FlightResponse])
    def search_flights(request: Request, origin: str = Query(pattern=r"^[A-Z]{3}$"), destination: str = Query(pattern=r"^[A-Z]{3}$"), date: Date | None = None):
        query = pb.SearchFlightsRequest(origin=origin, destination=destination)
        if date is not None:
            stamp = Timestamp()
            stamp.FromDatetime(datetime.combine(date, time(), tzinfo=timezone.utc))
            query.departure_date.CopyFrom(stamp)
        response = request.app.state.client.stub.SearchFlights(query)
        return [flight_json(flight) for flight in response.flights]

    @app.get("/flights/{flight_id}", response_model=FlightResponse)
    def get_flight(flight_id: UUID, request: Request):
        return flight_json(request.app.state.client.get(flight_id))

    @app.post("/bookings", response_model=BookingResponse, status_code=201)
    def create_booking(data: BookingCreate, request: Request):
        return request.app.state.service.create(data)

    @app.get("/bookings", response_model=list[BookingResponse])
    def list_bookings(user_id: UUID, request: Request):
        return request.app.state.service.list(user_id)

    @app.get("/bookings/{booking_id}", response_model=BookingResponse)
    def get_booking(booking_id: UUID, request: Request):
        return request.app.state.service.get(booking_id)

    @app.post("/bookings/{booking_id}/cancel", response_model=BookingResponse)
    def cancel_booking(booking_id: UUID, request: Request):
        return request.app.state.service.cancel(booking_id)

    return app


app = create_app()
