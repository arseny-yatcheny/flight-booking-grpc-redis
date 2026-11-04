from sqlalchemy import create_engine, text


def make_engine(url):
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=15000 -c lock_timeout=10000"},
    )


def lock_booking(connection, booking_id):
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"), {"id": str(booking_id)}
    )
