from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    flight_id: UUID
    passenger_name: str = Field(min_length=1, max_length=255)
    passenger_email: EmailStr = Field(max_length=254)
    seat_count: int = Field(gt=0, le=2147483647, strict=True)

    @field_validator("passenger_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Passenger name cannot be blank")
        return value


class BookingResponse(BaseModel):
    id: UUID
    user_id: UUID
    flight_id: UUID
    passenger_name: str
    passenger_email: str
    seat_count: int
    total_price: Decimal
    status: Literal["CONFIRMED", "CANCELLED"]
    created_at: datetime


class FlightResponse(BaseModel):
    id: UUID
    flight_number: str
    airline_code: str
    airline_name: str
    origin: str
    destination: str
    departure_at: datetime
    arrival_at: datetime
    total_seats: int
    available_seats: int
    price: Decimal
    status: Literal["SCHEDULED", "DEPARTED", "CANCELLED", "COMPLETED"]


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: list | None = None
