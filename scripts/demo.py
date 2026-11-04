import os
from uuid import uuid4

import httpx


base_url = os.getenv("BOOKING_URL", "http://localhost:8000")
user_id = str(uuid4())


def call(method, path, expected=200, **kwargs):
    response = httpx.request(method, f"{base_url}{path}", timeout=15, **kwargs)
    print(f"{method} {path}: {response.status_code}")
    if response.status_code != expected:
        raise RuntimeError(response.text)
    return response.json()


flights = call("GET", "/flights?origin=SVO&destination=LED")
if not flights:
    raise RuntimeError("No SVO-LED flights found")
flight = flights[0]
flight_id = flight["id"]
before = flight["available_seats"]
print(f"Рейс {flight['flight_number']}, вылет {flight['departure_at']}, свободно {before}, цена {flight['price']}")
call("GET", f"/flights/{flight_id}")
call("GET", f"/flights/{flight_id}")
body = {
    "user_id": user_id, "flight_id": flight_id, "passenger_name": "Ivan Petrov",
    "passenger_email": "ivan@example.com", "seat_count": 2,
}
booking = call("POST", "/bookings", expected=201, json=body)
booking_id = booking["id"]
assert call("GET", f"/flights/{flight_id}")["available_seats"] == before - 2
assert call("GET", f"/bookings/{booking_id}")["status"] == "CONFIRMED"
assert any(item["id"] == booking_id for item in call("GET", f"/bookings?user_id={user_id}"))
call("POST", "/bookings", expected=409, json={**body, "seat_count": flight["total_seats"] + 1})
call("POST", "/bookings", expected=422, json={**body, "seat_count": 0})
call("GET", f"/flights/{uuid4()}", expected=404)
assert call("POST", f"/bookings/{booking_id}/cancel")["status"] == "CANCELLED"
assert call("GET", f"/flights/{flight_id}")["available_seats"] == before
call("POST", f"/bookings/{booking_id}/cancel", expected=409)
print(f"booking_id={booking_id}")
print(f"user_id={user_id}")
print(f"SELECT id, status, total_price FROM bookings WHERE id = '{booking_id}';")
print(f"SELECT booking_id, status, seat_count FROM seat_reservations WHERE booking_id = '{booking_id}';")
print(f"SELECT id, available_seats FROM flights WHERE id = '{flight_id}';")
print("Демонстрация завершена")
