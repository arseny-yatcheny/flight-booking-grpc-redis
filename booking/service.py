from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

import grpc
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from booking.tables import bookings, operations
from common.logs import log


class BookingError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


@contextmanager
def booking_lock(engine, booking_id):
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:id, 0))"), {"id": str(booking_id)})
        connection.commit()
        try:
            yield connection
        finally:
            if connection.in_transaction():
                connection.rollback()
            if not connection.invalidated:
                try:
                    connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"), {"id": str(booking_id)})
                    connection.commit()
                except SQLAlchemyError:
                    connection.invalidate()


def recover_one(connection, client, booking_id):
    operation = connection.execute(select(operations).where(operations.c.booking_id == booking_id)).mappings().first()
    if operation is None:
        return
    booking = connection.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
    if operation["kind"] == "CREATE" and booking is not None:
        connection.execute(delete(operations).where(operations.c.booking_id == booking_id))
        return
    try:
        client.release(booking_id)
    except grpc.RpcError as error:
        if error.code() != grpc.StatusCode.NOT_FOUND:
            raise
    if operation["kind"] == "CANCEL":
        connection.execute(update(bookings).where(bookings.c.id == booking_id).values(status="CANCELLED"))
    connection.execute(delete(operations).where(operations.c.booking_id == booking_id))
    log("operation_recovered", booking_id=str(booking_id), kind=operation["kind"])


class BookingService:
    def __init__(self, engine, client):
        self.engine = engine
        self.client = client

    def get(self, booking_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
        if row is None:
            raise BookingError(404, "BOOKING_NOT_FOUND", "Booking not found")
        return dict(row)

    def list(self, user_id):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(bookings).where(bookings.c.user_id == user_id).order_by(bookings.c.created_at, bookings.c.id)).mappings()]

    def create(self, data):
        flight = self.client.get(data.flight_id)
        total = Decimal(flight.price) * data.seat_count
        if total > Decimal("9999999999999999.99"):
            raise BookingError(400, "INVALID_ARGUMENT", "Booking total exceeds the supported amount")
        booking_id = uuid4()
        with booking_lock(self.engine, booking_id) as connection:
            with connection.begin():
                connection.execute(insert(operations).values(booking_id=booking_id, kind="CREATE"))
            try:
                self.client.reserve(data.flight_id, booking_id, data.seat_count)
                with connection.begin():
                    row = connection.execute(insert(bookings).values(
                        id=booking_id, **data.model_dump(), total_price=total, status="CONFIRMED"
                    ).returning(bookings)).mappings().one()
                    connection.execute(delete(operations).where(operations.c.booking_id == booking_id))
                return dict(row)
            except (grpc.RpcError, SQLAlchemyError):
                if connection.in_transaction():
                    connection.rollback()
                try:
                    with connection.begin():
                        persisted = connection.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
                        if persisted is not None:
                            return dict(persisted)
                        recover_one(connection, self.client, booking_id)
                except (grpc.RpcError, SQLAlchemyError) as error:
                    log("recovery_pending", booking_id=str(booking_id), error=type(error).__name__)
                raise

    def cancel(self, booking_id):
        with booking_lock(self.engine, booking_id) as connection:
            with connection.begin():
                row = connection.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().first()
                if row is None:
                    raise BookingError(404, "BOOKING_NOT_FOUND", "Booking not found")
                if row["status"] != "CONFIRMED":
                    raise BookingError(409, "INVALID_BOOKING_STATE", "Booking is already cancelled")
                connection.execute(pg_insert(operations).values(booking_id=booking_id, kind="CANCEL").on_conflict_do_nothing())
            with connection.begin():
                recover_one(connection, self.client, booking_id)
                return dict(connection.execute(select(bookings).where(bookings.c.id == booking_id)).mappings().one())

    def recover(self):
        with self.engine.connect() as connection:
            pending = connection.execute(select(operations.c.booking_id).order_by(operations.c.created_at).limit(100)).scalars().all()
        for booking_id in pending:
            try:
                with self.engine.begin() as connection:
                    acquired = connection.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:id, 0))"), {"id": str(booking_id)}).scalar_one()
                    if acquired:
                        recover_one(connection, self.client, booking_id)
            except (grpc.RpcError, SQLAlchemyError) as error:
                log("recovery_pending", booking_id=str(booking_id), error=type(error).__name__)
