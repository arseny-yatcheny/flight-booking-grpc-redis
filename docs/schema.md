# Схема данных

## Flight Service

```mermaid
erDiagram
    AIRLINES ||--o{ FLIGHTS : operates
    AIRPORTS ||--o{ FLIGHTS : origin
    AIRPORTS ||--o{ FLIGHTS : destination
    FLIGHTS ||--o{ SEAT_RESERVATIONS : has

    AIRLINES {
        varchar code PK
        varchar name
    }
    AIRPORTS {
        varchar code PK
        varchar name
    }
    FLIGHTS {
        uuid id PK
        varchar flight_number
        varchar airline_code FK
        varchar origin FK
        varchar destination FK
        timestamptz departure_at
        timestamptz arrival_at
        integer total_seats
        integer available_seats
        numeric price
        varchar status
    }
    SEAT_RESERVATIONS {
        uuid id PK
        uuid flight_id FK
        uuid booking_id UK
        integer seat_count
        varchar status
        timestamptz created_at
    }
    RESERVATION_CANCELLATIONS {
        uuid booking_id PK
        timestamptz created_at
    }
    CACHE_EPOCH {
        integer id PK
        bigint version
    }
```

## Booking Service

```mermaid
erDiagram
    BOOKINGS {
        uuid id PK
        uuid user_id
        uuid flight_id
        varchar passenger_name
        varchar passenger_email
        integer seat_count
        numeric total_price
        varchar status
        timestamptz created_at
    }
    BOOKING_OPERATIONS {
        uuid booking_id PK
        varchar kind
        timestamptz created_at
    }
```

`SEAT_RESERVATIONS.booking_id` ссылается на `BOOKINGS.id` логически: базы находятся в разных сервисах, физического FK между ними нет. Для каждого успешно подтверждённого бронирования создаётся одна резервация. Уникальность `booking_id` исключает второй резерв. Освобождённые резервации не удаляются: они обеспечивают идемпотентность повторной отмены.

`BOOKINGS.flight_id` — логическая ссылка на `FLIGHTS.id`. `user_id` — внешний идентификатор пользователя. Пассажирские данные относятся к конкретному бронированию: пользователь может оформить разные бронирования для разных пассажиров.

## 3NF

Поля атомарны. В каждой таблице есть первичный ключ, а неключевые поля зависят от идентификатора своей сущности. Названия аэропортов и авиакомпаний вынесены в справочники и не дублируются в рейсах. Booking Service хранит ID рейса, а не копии его маршрута, названия авиакомпании и расписания.

`total_price` — историческая стоимость конкретного бронирования. Она не зависит от будущих изменений `FLIGHTS.price`. `available_seats` — требуемый заданием счётчик остатков, согласованность которого с резервами поддерживается одной транзакцией.

Номер рейса вместе с календарной датой вылета UTC уникален. Для этого создан уникальный индекс по выражению, без отдельного дублирующего поля даты.

`BOOKING_OPERATIONS` — журнал восстановления до появления бронирования или завершения отмены. `RESERVATION_CANCELLATIONS` блокирует опоздавшее резервирование после компенсации. `CACHE_EPOCH` — одна служебная строка для версии кеша. Эти таблицы не хранят копии бизнес-данных.

## Ограничения

- `total_seats > 0`, `0 <= available_seats <= total_seats`, `seat_count > 0`.
- `price > 0`, `total_price > 0`.
- Аэропорты вылета и прилёта различаются, время прилёта позже вылета.
- Аэропорты имеют IATA-коды из трёх заглавных латинских букв.
- Статусы проверяются ограничениями `CHECK` в PostgreSQL и enum в gRPC-контракте.
- Связи внутри Flight Service защищены внешними ключами.
- `seat_reservations.booking_id` уникален.
- Поиск рейсов и бронирования пользователя поддержаны индексами.
