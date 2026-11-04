import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import grpc
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import flights_pb2 as pb
import flights_pb2_grpc as rpc
from booking.client import FlightClient
from booking.main import create_app
from booking.resilience import CircuitBreaker, CircuitOpen, ResilienceInterceptor, RetrySettings
from flight.auth import ApiKeyInterceptor
from flight.errors import ErrorInterceptor
from flight.service import FlightService
from flight.tables import flights, reservations


@contextmanager
def grpc_server(service):
    server = grpc.server(ThreadPoolExecutor(max_workers=8), interceptors=[ApiKeyInterceptor("test-key"), ErrorInterceptor()])
    rpc.add_FlightServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.stop(1).wait()


class UnstableFlight(rpc.FlightServiceServicer):
    def __init__(self, codes):
        self.codes = codes
        self.calls = 0
        self.lock = threading.Lock()

    def GetFlight(self, request, context):
        with self.lock:
            code = self.codes[min(self.calls, len(self.codes) - 1)]
            self.calls += 1
        if code == "timeout":
            time.sleep(0.1)
        elif code != grpc.StatusCode.OK:
            context.abort(code, code.name)
        return pb.Flight(id=request.flight_id, price="10.00", status=pb.SCHEDULED)


@pytest.mark.parametrize("code", [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED])
def test_retry_backoff_and_three_attempt_limit(code):
    server = UnstableFlight([code])
    sleeps = []
    with grpc_server(server) as target:
        interceptor = ResilienceInterceptor("test-key", CircuitBreaker(), RetrySettings(), sleep=sleeps.append)
        with grpc.intercept_channel(grpc.insecure_channel(target, options=[("grpc.enable_retries", 0)]), interceptor) as channel:
            with pytest.raises(grpc.RpcError) as caught:
                rpc.FlightServiceStub(channel).GetFlight(pb.GetFlightRequest(flight_id=str(uuid4())))
    assert caught.value.code() == code
    assert server.calls == 3
    assert sleeps == [0.1, 0.2]


@pytest.mark.parametrize("code", [grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.FAILED_PRECONDITION])
def test_business_errors_are_not_retried(code):
    server = UnstableFlight([code])
    breaker = CircuitBreaker(threshold=1)
    with grpc_server(server) as target:
        client = FlightClient(target, "test-key", RetrySettings(backoff=0), breaker)
        with pytest.raises(grpc.RpcError) as caught:
            client.get(uuid4())
        client.close()
    assert caught.value.code() == code
    assert server.calls == 1
    assert breaker.state == "CLOSED"


def test_retries_succeed_on_third_attempt():
    server = UnstableFlight([grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.OK])
    with grpc_server(server) as target:
        client = FlightClient(target, "test-key", RetrySettings(backoff=0))
        assert client.get(uuid4()).price == "10.00"
        client.close()
    assert server.calls == 3


def test_actual_rpc_deadline_is_enforced():
    server = UnstableFlight(["timeout"])
    with grpc_server(server) as target:
        client = FlightClient(target, "test-key", RetrySettings(timeout=0.03, backoff=0))
        with pytest.raises(grpc.RpcError) as caught:
            client.get(uuid4())
        client.close()
    assert caught.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    assert server.calls == 3


def test_circuit_open_fast_failure_and_half_open_success(booking_engine):
    clock = [0.0]
    breaker = CircuitBreaker(threshold=1, reset_timeout=10, clock=lambda: clock[0])
    server = UnstableFlight([grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.OK])
    with grpc_server(server) as target:
        client = FlightClient(target, "test-key", RetrySettings(attempts=1), breaker)
        with TestClient(create_app(booking_engine, client, recover=False)) as api:
            assert api.get(f"/flights/{uuid4()}").status_code == 503
            assert breaker.state == "OPEN"
            before = time.monotonic()
            response = api.get(f"/flights/{uuid4()}")
            assert time.monotonic() - before < 0.5
            assert response.status_code == 503
            assert response.json()["error_code"] == "FLIGHT_SERVICE_UNAVAILABLE"
            assert server.calls == 1
            clock[0] = 11
            assert client.get(uuid4()).price == "10.00"
            assert server.calls == 2
            assert breaker.state == "CLOSED"
        client.close()


def test_half_open_allows_only_one_probe_and_reopens():
    clock = [0.0]
    breaker = CircuitBreaker(threshold=1, reset_timeout=10, clock=lambda: clock[0])
    breaker.finish(breaker.acquire(), True)
    clock[0] = 11
    probe = breaker.acquire()
    assert breaker.state == "HALF_OPEN"
    with pytest.raises(CircuitOpen):
        breaker.acquire()
    breaker.finish(probe, True)
    assert breaker.state == "OPEN"
    with pytest.raises(CircuitOpen):
        breaker.acquire()


def test_expired_failures_and_old_inflight_success_do_not_corrupt_breaker():
    clock = [0.0]
    breaker = CircuitBreaker(threshold=2, window=5, clock=lambda: clock[0])
    breaker.finish(breaker.acquire(), True)
    clock[0] = 6
    breaker.finish(breaker.acquire(), True)
    assert breaker.state == "CLOSED"
    stale_success = breaker.acquire()
    breaker.finish(breaker.acquire(), True)
    assert breaker.state == "OPEN"
    breaker.finish(stale_success, False)
    assert breaker.state == "OPEN"


def test_lost_reservation_response_is_retried_without_double_reservation(flight_engine, cache, make_flight):
    class LostReplyService(FlightService):
        calls = 0

        def ReserveSeats(self, request, context):
            result = super().ReserveSeats(request, context)
            self.calls += 1
            if self.calls == 1:
                context.abort(grpc.StatusCode.UNAVAILABLE, "Response lost after commit")
            return result

    flight = make_flight()
    service = LostReplyService(flight_engine, cache)
    with grpc_server(service) as target:
        client = FlightClient(target, "test-key", RetrySettings(backoff=0))
        reservation = client.reserve(flight["id"], uuid4(), 2)
        assert reservation.status == pb.ACTIVE
        client.close()
    assert service.calls == 2
    with flight_engine.connect() as connection:
        assert connection.execute(select(flights.c.available_seats)).scalar_one() == 8
        assert connection.execute(select(func.count()).select_from(reservations)).scalar_one() == 1
