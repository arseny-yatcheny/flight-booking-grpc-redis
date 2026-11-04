from datetime import timezone

from google.protobuf.message import DecodeError
from redis import RedisError
from redis.sentinel import Sentinel

from common.logs import log


class FlightCache:
    def __init__(self, nodes, master_name="flights", ttl=600):
        if not 300 <= ttl <= 600:
            raise ValueError("CACHE_TTL_SECONDS must be between 300 and 600")
        addresses = [(host, int(port)) for host, port in (node.rsplit(":", 1) for node in nodes.split(","))]
        self.sentinel = Sentinel(
            addresses,
            socket_timeout=0.3,
            socket_connect_timeout=0.3,
            sentinel_kwargs={"socket_timeout": 0.3, "socket_connect_timeout": 0.3},
        )
        self.client = self.sentinel.master_for(master_name, socket_timeout=0.3, socket_connect_timeout=0.3)
        self.ttl = ttl

    def get(self, key, version, message_type):
        try:
            value = self.client.get(key)
            if value:
                cached_version, payload = value.split(b"\n", 1)
                if int(cached_version) == version:
                    result = message_type.FromString(payload)
                    log("cache_hit", key=key)
                    return result
            log("cache_miss", key=key)
        except (RedisError, ValueError, DecodeError) as error:
            log("cache_unavailable", operation="get", error=type(error).__name__)
        return None

    def put(self, key, version, message):
        try:
            self.client.set(key, str(version).encode() + b"\n" + message.SerializeToString(), ex=self.ttl)
        except RedisError as error:
            log("cache_unavailable", operation="set", error=type(error).__name__)

    def invalidate(self, flight):
        date = flight["departure_at"].astimezone(timezone.utc).date().isoformat()
        prefix = f"search:{flight['origin']}:{flight['destination']}"
        try:
            self.client.delete(f"flight:{flight['id']}", f"{prefix}:{date}", f"{prefix}:all")
            log("cache_invalidated", flight_id=str(flight["id"]))
        except RedisError as error:
            log("cache_unavailable", operation="delete", error=type(error).__name__)
