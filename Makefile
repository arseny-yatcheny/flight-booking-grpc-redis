.PHONY: up down generate demo test test-down
up:
	docker compose up --build -d --wait
down:
	docker compose down
generate:
	python3 scripts/generate.py
demo:
	docker compose exec booking python scripts/demo.py
test:
	docker compose -p flight-booking-tests -f compose.yaml -f compose.test.yaml up --build -d --wait
	docker compose -p flight-booking-tests -f compose.yaml -f compose.test.yaml run --rm tests
test-down:
	docker compose -p flight-booking-tests -f compose.yaml -f compose.test.yaml down -v
