INSERT INTO airlines (code, name) VALUES ('SU', 'Aeroflot');
INSERT INTO airports (code, name) VALUES ('SVO', 'Sheremetyevo'), ('LED', 'Pulkovo'), ('VKO', 'Vnukovo');

INSERT INTO flights (id, flight_number, airline_code, origin, destination, departure_at, arrival_at, total_seats, available_seats, price, status)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'SU1234', 'SU', 'SVO', 'LED',
     (CURRENT_DATE + 7 + TIME '10:00') AT TIME ZONE 'UTC', (CURRENT_DATE + 7 + TIME '11:30') AT TIME ZONE 'UTC', 100, 100, 5000.00, 'SCHEDULED'),
    ('22222222-2222-4222-8222-222222222222', 'SU1235', 'SU', 'SVO', 'LED',
     (CURRENT_DATE + 8 + TIME '15:00') AT TIME ZONE 'UTC', (CURRENT_DATE + 8 + TIME '16:30') AT TIME ZONE 'UTC', 50, 50, 7500.00, 'SCHEDULED');
