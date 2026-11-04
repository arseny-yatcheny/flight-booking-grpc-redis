import threading
import time
from collections import deque, namedtuple
from dataclasses import dataclass

import grpc

from common.logs import log


TRANSIENT_CODES = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}


class CircuitOpen(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "Flight Service circuit breaker is open; try again later"


@dataclass(frozen=True)
class RetrySettings:
    attempts: int = 3
    backoff: float = 0.1
    timeout: float = 2.0

    def __post_init__(self):
        if not 1 <= self.attempts <= 3 or self.backoff < 0 or self.timeout <= 0:
            raise ValueError("Invalid gRPC retry settings")


class CircuitBreaker:
    def __init__(self, threshold=5, reset_timeout=10.0, window=30.0, clock=time.monotonic):
        if threshold < 1 or reset_timeout <= 0 or window <= 0:
            raise ValueError("Invalid circuit breaker settings")
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.window = window
        self.clock = clock
        self.state = "CLOSED"
        self.failures = deque()
        self.opened_at = 0.0
        self.generation = 0
        self.lock = threading.Lock()

    def transition(self, state):
        log("circuit_transition", previous=self.state, current=state)
        self.state = state
        self.generation += 1
        if state == "OPEN":
            self.opened_at = self.clock()

    def acquire(self):
        with self.lock:
            if self.state == "OPEN":
                if self.clock() - self.opened_at < self.reset_timeout:
                    raise CircuitOpen()
                self.transition("HALF_OPEN")
                return self.generation, True
            if self.state == "HALF_OPEN":
                raise CircuitOpen()
            return self.generation, False

    def finish(self, ticket, failed):
        generation, probe = ticket
        with self.lock:
            if generation != self.generation:
                return
            if probe:
                self.failures.clear()
                self.transition("OPEN" if failed else "CLOSED")
                return
            if not failed:
                self.failures.clear()
                return
            now = self.clock()
            while self.failures and self.failures[0] < now - self.window:
                self.failures.popleft()
            self.failures.append(now)
            if len(self.failures) >= self.threshold:
                self.transition("OPEN")


class CallDetails(namedtuple("CallDetailsBase", "method timeout metadata credentials wait_for_ready compression"), grpc.ClientCallDetails):
    pass


class ResilienceInterceptor(grpc.UnaryUnaryClientInterceptor):
    def __init__(self, api_key, breaker, settings, sleep=time.sleep):
        self.api_key = api_key
        self.breaker = breaker
        self.settings = settings
        self.sleep = sleep

    def intercept_unary_unary(self, continuation, details, request):
        ticket = self.breaker.acquire()
        attempts = 1 if ticket[1] else self.settings.attempts
        metadata = list(details.metadata or []) + [("x-api-key", self.api_key)]
        call_details = CallDetails(
            details.method, self.settings.timeout, metadata, details.credentials,
            False, getattr(details, "compression", None),
        )
        try:
            for attempt in range(attempts):
                result = continuation(call_details, request)
                code = result.code()
                if code not in TRANSIENT_CODES or attempt + 1 == attempts:
                    self.breaker.finish(ticket, code in TRANSIENT_CODES)
                    return result
                delay = self.settings.backoff * 2 ** attempt
                log("grpc_retry", method=details.method, attempt=attempt + 1, code=code.name, delay_seconds=delay)
                self.sleep(delay)
        except BaseException:
            self.breaker.finish(ticket, True)
            raise
