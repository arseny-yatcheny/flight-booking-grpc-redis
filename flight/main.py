import os
import signal
import threading
from concurrent.futures import ThreadPoolExecutor

import grpc

import flights_pb2_grpc as rpc
from common.database import make_engine
from common.logs import configure_logging, log
from flight.auth import ApiKeyInterceptor
from flight.cache import FlightCache
from flight.errors import ErrorInterceptor
from flight.service import FlightService


def main():
    configure_logging()
    engine = make_engine(os.environ["DATABASE_URL"])
    cache = FlightCache(os.environ["REDIS_SENTINELS"], os.getenv("REDIS_MASTER_NAME", "flights"), int(os.getenv("CACHE_TTL_SECONDS", "600")))
    server = grpc.server(ThreadPoolExecutor(max_workers=16), interceptors=[ApiKeyInterceptor(os.environ["GRPC_API_KEY"]), ErrorInterceptor()])
    rpc.add_FlightServiceServicer_to_server(FlightService(engine, cache), server)
    server.add_insecure_port("[::]:50051")
    stopped = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stopped.set())
    server.start()
    log("flight_started", port=50051)
    stopped.wait()
    server.stop(10).wait()
    cache.client.close()
    engine.dispose()


if __name__ == "__main__":
    main()
