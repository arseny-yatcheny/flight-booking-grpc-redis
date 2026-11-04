import os

import grpc

import flights_pb2 as pb
import flights_pb2_grpc as rpc
from booking.resilience import CircuitBreaker, ResilienceInterceptor, RetrySettings


class FlightClient:
    def __init__(self, target, api_key, settings=None, breaker=None):
        if not api_key:
            raise ValueError("GRPC_API_KEY is required")
        self.breaker = breaker or CircuitBreaker()
        self.channel = grpc.intercept_channel(
            grpc.insecure_channel(target, options=[("grpc.enable_retries", 0)]),
            ResilienceInterceptor(api_key, self.breaker, settings or RetrySettings()),
        )
        self.stub = rpc.FlightServiceStub(self.channel)

    @classmethod
    def from_environment(cls):
        return cls(
            os.environ["FLIGHT_GRPC_TARGET"], os.environ["GRPC_API_KEY"],
            RetrySettings(int(os.getenv("GRPC_MAX_ATTEMPTS", "3")), float(os.getenv("GRPC_RETRY_BACKOFF", "0.1")), float(os.getenv("GRPC_TIMEOUT_SECONDS", "2"))),
            CircuitBreaker(int(os.getenv("CB_FAILURE_THRESHOLD", "5")), float(os.getenv("CB_RESET_TIMEOUT", "10")), float(os.getenv("CB_WINDOW_SECONDS", "30"))),
        )

    def get(self, flight_id):
        return self.stub.GetFlight(pb.GetFlightRequest(flight_id=str(flight_id)))

    def reserve(self, flight_id, booking_id, count):
        return self.stub.ReserveSeats(pb.ReserveSeatsRequest(flight_id=str(flight_id), booking_id=str(booking_id), seat_count=count))

    def release(self, booking_id):
        return self.stub.ReleaseReservation(pb.ReleaseReservationRequest(booking_id=str(booking_id)))

    def close(self):
        self.channel.close()
