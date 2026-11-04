import json
import logging
from datetime import datetime, timezone


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log(event, **fields):
    logging.getLogger("flight_booking").info(
        json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, default=str)
    )
