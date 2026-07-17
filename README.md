# Сервис асинхронного процессинга платежей

## Оглавление

1. [О чём проект?](#о-чём-проект)
2. [Что умеет сервис?](#что-умеет-сервис)
3. [Какой стек используется?](#какой-стек-используется)
4. [Как запустить в Docker?](#как-запустить-в-docker)
5. [Какие контейнеры поднимаются?](#какие-контейнеры-поднимаются)
6. [Как проверить сервис после запуска?](#как-проверить-сервис-после-запуска)
7. [Как посмотреть входящие webhook-запросы?](#как-посмотреть-входящие-webhook-запросы)
8. [Как устроена архитектура?](#как-устроена-архитектура)
9. [Как устроена модель платежа?](#как-устроена-модель-платежа)
10. [Как работает аутентификация?](#как-работает-аутентификация)
11. [Как работает HTTP-идемпотентность?](#как-работает-http-идемпотентность)
12. [Зачем нужен transactional outbox?](#зачем-нужен-transactional-outbox)
13. [Как работает RabbitMQ topology?](#как-работает-rabbitmq-topology)
14. [Как работает consumer?](#как-работает-consumer)
15. [Как работает retry и DLQ?](#как-работает-retry-и-dlq)
16. [Как работают webhook-уведомления?](#как-работают-webhook-уведомления)
17. [Какие гарантии доставки есть у сервиса?](#какие-гарантии-доставки-есть-у-сервиса)
18. [Как устроено логирование?](#как-устроено-логирование)
19. [Какие технические решения и компромиссы приняты?](#какие-технические-решения-и-компромиссы-приняты)
20. [Что можно улучшить для production?](#что-можно-улучшить-для-production)
21. [Как запустить локальную разработку?](#как-запустить-локальную-разработку)
22. [Как запустить тесты и линтер?](#как-запустить-тесты-и-линтер)
23. [Где лежат ключевые файлы?](#где-лежат-ключевые-файлы)

## О чём проект?

Это тестовый проект: микросервис асинхронного процессинга платежей.

Сервис принимает HTTP-запрос на создание платежа, сохраняет платёж в PostgreSQL со статусом
`pending`, в той же транзакции создаёт событие в transactional outbox, а затем отдельный
outbox publisher публикует событие в RabbitMQ. FastStream consumer получает событие, эмулирует
обращение к платёжному шлюзу, переводит платёж в `succeeded` или `failed` и отправляет webhook.

Ключевая идея проекта: HTTP API не публикует события напрямую в RabbitMQ. Надёжность связи между
PostgreSQL и RabbitMQ обеспечивается через transactional outbox.

[К оглавлению](#оглавление)

## Что умеет сервис?

Сервис поддерживает три HTTP endpoint:

| Метод | Путь | Назначение |
| --- | --- | --- |
| `GET` | `/health` | Проверка работоспособности |
| `POST` | `/api/v1/payments` | Создание платежа, требует `Idempotency-Key`, возвращает `202 Accepted` |
| `GET` | `/api/v1/payments/{payment_id}` | Получение информации о платеже |

Основной сценарий:

1. Клиент вызывает `POST /api/v1/payments`.
2. API проверяет `X-API-Key` и обязательный `Idempotency-Key`.
3. В PostgreSQL одной транзакцией создаются `payments` и `outbox`.
4. Отдельный outbox publisher публикует событие `payments.new` в RabbitMQ.
5. Consumer обрабатывает событие, вызывает mock gateway, меняет статус платежа.
6. Consumer отправляет webhook на сохранённый URL.
7. Ошибки уходят в retry, потом в DLQ очередь.

[К оглавлению](#оглавление)

## Какой стек используется?

- `uv` для зависимостей и запуска команд.
- FastAPI для HTTP API.
- Pydantic v2 и Pydantic Settings.
- SQLAlchemy 2.x async ORM.
- asyncpg.
- PostgreSQL.
- Alembic.
- RabbitMQ.
- FastStream.
- httpx.AsyncClient.
- pytest и pytest-asyncio.
- Ruff.
- Docker Compose.

[К оглавлению](#оглавление)

## Как запустить в Docker?

Минимальная команда:

```bash
docker compose up --build
```

Для запуска с ожиданием healthcheck:

```bash
docker compose up --build --wait
```

Файл `.env` необязателен. Docker Compose импортирует его, если файл существует, но проект работает
и без него. Значения по умолчанию согласованы между `docker-compose.yml` и Pydantic Settings.

При желании можно создать `.env` из примера:

```bash
cp .env.example .env
```

Но для стандартного локального запуска это не обязательно.

После запуска:

- API доступен на `http://localhost:8000`;
- Swagger доступен на `http://localhost:8000/docs`;
- OpenAPI JSON доступен на `http://localhost:8000/openapi.json`;
- RabbitMQ Management UI доступен на `http://localhost:15672`;
- RabbitMQ credentials по умолчанию: `guest` / `guest`.
- webhook echo-service доступен с хоста на `http://localhost:9000` и из Docker network как
  `http://webhook-echo:9000`.

Остановка:

```bash
docker compose down
```

Если нужно удалить volume с данными PostgreSQL:

```bash
docker compose down -v
```

[К оглавлению](#оглавление)

## Какие контейнеры поднимаются?

`docker compose up --build` поднимает:

| Сервис | Назначение |
| --- | --- |
| `postgres` | PostgreSQL runtime database |
| `rabbitmq` | RabbitMQ broker с management UI |
| `migrate` | Одноразовый контейнер с `alembic upgrade head` |
| `api` | FastAPI приложение |
| `outbox-publisher` | Отдельный процесс transactional outbox relay |
| `consumer` | FastStream consumer платежных событий |
| `webhook-echo` | Dev-сервис, который принимает webhook на любой path и логирует запрос |

Порядок старта:

1. `postgres` и `rabbitmq` проходят healthcheck.
2. `migrate` применяет Alembic migrations.
3. `api`, `outbox-publisher` и `consumer` стартуют после успешных миграций.

[К оглавлению](#оглавление)

## Как проверить сервис после запуска?

### Как проверить, что API защищён ключом?

Запрос без `X-API-Key`:

```bash
curl -i http://localhost:8000/health
```

Ожидаемый результат:

```text
HTTP/1.1 401 Unauthorized
```

Запрос с ключом:

```bash
curl -i http://localhost:8000/health \
  -H "X-API-Key: change-me"
```

Ожидаемый результат:

```text
HTTP/1.1 200 OK
```

### Как создать платёж?

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -H "Idempotency-Key: demo-payment-1" \
  -d '{
    "amount": "1250.50",
    "currency": "RUB",
    "description": "Order 42",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://webhook-echo:9000/webhooks/payments"
  }'
```

Ожидаемый результат:

```text
HTTP/1.1 202 Accepted
```

Тело ответа содержит `payment_id`, `status` и `created_at`.

`webhook_url` в примере указывает на `webhook-echo`, поэтому после обработки платежа можно увидеть
полный webhook-запрос в логах контейнера `webhook-echo`.

<img width="665" height="303" alt="Image" src="https://github.com/user-attachments/assets/6bc965c8-7800-4e40-8016-a3a624951149" />

<img width="1394" height="512" alt="Image" src="https://github.com/user-attachments/assets/abfd7cbe-0059-4cd9-9306-f2dcc1671f09" />

### Как проверить идемпотентный повтор?

Повторите тот же запрос с тем же `Idempotency-Key` и тем же JSON body:

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -H "Idempotency-Key: demo-payment-1" \
  -d '{
    "amount": "1250.50",
    "currency": "RUB",
    "description": "Order 42",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://webhook-echo:9000/webhooks/payments"
  }'
```

Ожидаемый результат: `202 Accepted` и тот же `payment_id`.

### Как проверить конфликт идемпотентности?

Повторите запрос с тем же `Idempotency-Key`, но измените payload:

```bash
curl -i -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me" \
  -H "Idempotency-Key: demo-payment-1" \
  -d '{
    "amount": "1251.00",
    "currency": "RUB",
    "description": "Order 42",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://webhook-echo:9000/webhooks/payments"
  }'
```

Ожидаемый результат:

```text
HTTP/1.1 409 Conflict
```

### Как получить платёж?

```bash
curl -i http://localhost:8000/api/v1/payments/{payment_id} \
  -H "X-API-Key: change-me"
```

[К оглавлению](#оглавление)

## Как посмотреть входящие webhook-запросы?

Для удобной ручной проверки в Docker Compose добавлен `webhook-echo`.

Он принимает любой HTTP method на любой path, логирует запрос и возвращает JSON с тем, что получил:

- method;
- URL;
- path;
- query params;
- headers;
- client;
- body;
- body size.

Healthcheck:

```bash
curl -i http://localhost:9000/health
```

Пример ручного запроса:

```bash
curl -i -X POST "http://localhost:9000/some/path?foo=bar" \
  -H "X-Test: value" \
  -H "Content-Type: application/json" \
  -d '{"hello": "world"}'
```

Посмотреть webhook-логи:

```bash
docker compose logs -f webhook-echo
```

Событие в логах называется:

```text
webhook_echo_request_received
```

В Swagger/OpenAPI поле `webhook_url` показывает default/example:

```text
http://webhook-echo:9000/webhooks/payments
```

Это значение добавлено именно на уровне OpenAPI-схемы. В Pydantic-модели `PaymentCreate` поле
`webhook_url` остаётся обязательным и не имеет runtime default.

[К оглавлению](#оглавление)


## Как устроена архитектура?

Архитектура layered / DDD-lite:

```text
Presentation / FastAPI
        -> Application / use cases and ports
        -> Domain / payment rules
        -> Infrastructure / PostgreSQL, RabbitMQ, HTTP adapters
```

Слои:

- `presentation`: FastAPI routers, dependencies, exception handlers, wiring сервисов.
- `schemas`: Pydantic request/response models.
- `application`: use cases, ports, idempotency, outbox contracts, webhook payload logic.
- `domain`: `Payment`, `PaymentStatus`, `Currency`, доменные исключения и переходы статусов.
- `infrastructure`: SQLAlchemy repositories, Unit of Work, RabbitMQ topology, outbox publisher,
  consumer, mock gateway, webhook client.

API, outbox publisher и consumer являются адаптерами. Основная бизнес-логика находится в domain и
application слоях.

[К оглавлению](#оглавление)

## Как устроена модель платежа?

Платёж содержит:

- UUID;
- `Decimal` amount;
- currency: `RUB`, `USD`, `EUR`;
- description;
- metadata JSON;
- webhook URL;
- status: `pending`, `succeeded`, `failed`;
- idempotency key;
- request fingerprint;
- `created_at`;
- `processed_at`;
- `webhook_delivered_at`;
- `webhook_attempts`;
- `last_webhook_error`.

Деньги не хранятся как `float`. В домене используется `Decimal`, в PostgreSQL используется
`NUMERIC(18, 2)`. Это исключает типичные ошибки округления при работе с денежными значениями.

[К оглавлению](#оглавление)

## Как работает аутентификация?

Все HTTP endpoints требуют header:

```text
X-API-Key: change-me
```

`change-me` — локальное значение по умолчанию. Для production его нужно заменить через env.

Неверный или отсутствующий ключ возвращает `401 Unauthorized`. Значение ключа не логируется.

[К оглавлению](#оглавление)

## Как работает HTTP-идемпотентность?

`POST /api/v1/payments` требует header:

```text
Idempotency-Key: some-client-key
```

Поведение:

- первый запрос создаёт один payment и один outbox event;
- тот же key и тот же payload возвращают тот же payment;
- тот же key и другой payload возвращают `409 Conflict`;
- конкурентные повторы с одним key не создают дубли.

Для проверки payload используется request fingerprint. Для защиты от гонок используется уникальный
индекс в PostgreSQL по `idempotency_key` и application-level обработка `PersistenceConflictError`.

[К оглавлению](#оглавление)

## Зачем нужен transactional outbox?

Transactional outbox нужен, чтобы не потерять событие между PostgreSQL и RabbitMQ.

При создании платежа в одной транзакции создаются:

1. row в `payments`;
2. row в `outbox`.

API не публикует сообщение в RabbitMQ напрямую. После commit отдельный `outbox-publisher` читает
неопубликованные outbox rows и отправляет события в RabbitMQ.

Если RabbitMQ недоступен после commit, outbox row остаётся в PostgreSQL со статусом `pending` и
будет опубликован позже.

Outbox publisher выбирает batch через `FOR UPDATE SKIP LOCKED`, поэтому несколько publisher-ов могут
работать параллельно и не брать одни и те же rows.

Outbox row становится `published` только после успешной публикации. При ошибке сохраняются
`attempts` и `last_error`.

Текущий relay обрабатывает небольшой batch в одной database transaction:

```text
BEGIN
  SELECT pending outbox rows
    FOR UPDATE SKIP LOCKED
    LIMIT OUTBOX_BATCH_SIZE

  for each event:
    publish to RabbitMQ
    if confirmed:
      mark outbox row as published
    if failed:
      increment attempts and store last_error

COMMIT
```

Гарантии текущей реализации:

- создание `payment + outbox` атомарно;
- несколько relay-процессов могут работать одновременно благодаря `FOR UPDATE SKIP LOCKED`;
- outbox row помечается `published` только после успешной публикации в RabbitMQ;
- если RabbitMQ недоступен, row остаётся `pending` и будет подобран позже;
- опубликованные outbox rows остаются в базе как журнал.

Модель доставки — at-least-once, не exactly-once.

Компромисс текущей реализации: database transaction остаётся открытой на время публикации batch в
RabbitMQ. Для тестового задания это простой и проверяемый вариант, но в production это может быть
дороже:

- row locks удерживаются во время network calls к RabbitMQ;
- медленный broker может увеличить время жизни transaction;
- если процесс упадёт после успешной публикации, но до `COMMIT`, row останется `pending`, и сообщение
  может быть опубликовано повторно;
- если падение произойдёт ближе к концу большого batch, несколько уже опубликованных, но ещё не
  закоммиченных отметок `published` откатятся.

Такие дубли ожидаемы для outbox-подхода. Поэтому consumer идемпотентен: финальный платёж не
проводится через gateway повторно, а уже доставленный webhook обычно не отправляется снова.

[К оглавлению](#оглавление)

## Как работает RabbitMQ topology?

<img width="1056" height="320" alt="Image" src="https://github.com/user-attachments/assets/56bfe7a6-0d73-43be-8974-8a65b62e82c6" />

Основные сущности:

- exchange `payments.events`;
- routing key `payments.new`;
- queue `payments.new`;
- retry exchange `payments.retry`;
- retry queues `payments.new.retry.2` и `payments.new.retry.3`;
- dead-letter exchange `payments.dlx`;
- dead-letter queue `payments.dlq`.

Все exchanges и queues объявляются durable.

Retry queues используют TTL и dead-letter routing обратно в `payments.events` с routing key
`payments.new`.

Topology объявляется приложением при старте consumer-а. Не нужно создавать exchanges, queues и
bindings вручную через RabbitMQ UI вместо application declaration: UI в этом проекте нужен для
наблюдения и отладки.

RabbitMQ Management UI доступен локально:

```text
http://localhost:15672
guest / guest
```

Эти credentials являются development-only дефолтами официального RabbitMQ image и не должны
использоваться в production.

[К оглавлению](#оглавление)

## Как работает consumer?

FastStream consumer читает `payments.new`.

Flow:

```text
получить сообщение
  -> провалидировать payload
  -> взять payment row с lock
  -> если status pending: вызвать mock gateway
  -> выставить succeeded или failed
  -> commit
  -> отправить webhook
  -> ack
```

Если платёж уже в финальном статусе, gateway повторно не вызывается. Если webhook ещё не доставлен,
повторное сообщение запускает только webhook stage.

Malformed message и missing payment отправляются в DLQ и ack-аются.

[К оглавлению](#оглавление)

## Как работает retry и DLQ?

По умолчанию разрешены три суммарные попытки.

При `RETRY_BASE_DELAY_SECONDS=1`:

1. attempt 1 получает transient error и уходит в `payments.new.retry.2` на 1 секунду;
2. attempt 2 получает transient error и уходит в `payments.new.retry.3` на 2 секунды;
3. attempt 3 получает ошибку и уходит в `payments.dlq`.

Permanent errors не ретраятся и сразу отправляются в DLQ.

Retry реализован через RabbitMQ TTL/DLX. Consumer не делает долгий `sleep` и не держит unacked
message.

Автоматический redrive из DLQ не реализован. Для ручного redrive в dev-режиме можно посмотреть body
сообщения в RabbitMQ Management UI и переопубликовать его в `payments.events` с routing key
`payments.new`.

[К оглавлению](#оглавление)

## Как работают webhook-уведомления?

После обработки платежа consumer отправляет webhook на URL из payment.

Payload:

```json
{
  "payment_id": "uuid",
  "status": "succeeded",
  "processed_at": "2026-07-16T17:21:53.415045+00:00"
}
```

Header:

```text
X-Webhook-Id: payment:{payment_id}:status-webhook
```

Успехом считается любой HTTP `2xx`.

Transient errors:

- network error;
- timeout;
- HTTP `408`;
- HTTP `429`;
- HTTP `5xx`.

Permanent errors:

- остальные HTTP `4xx`.

[К оглавлению](#оглавление)

## Какие гарантии доставки есть у сервиса?

Для событий `payments.new` гарантия — at-least-once.

Дубликаты возможны, например если outbox publisher успешно отправил сообщение в RabbitMQ, но упал
до записи `published` в PostgreSQL. Это нормальный компромисс outbox-паттерна.

Consumer идемпотентен:

- финальный payment не отправляется в gateway повторно;
- доставленный webhook не отправляется повторно при обычном дубле сообщения;
- если payment финальный, но webhook не доставлен, повторяется только webhook stage.

Exactly-once webhook не обещается. Процесс может упасть после успешного HTTP-вызова webhook, но до
сохранения `webhook_delivered_at`. Поэтому отправляется стабильный `X-Webhook-Id`, чтобы получатель
мог дедуплицировать редкие повторы.

[К оглавлению](#оглавление)

## Как устроено логирование?

Логи структурированные, JSON-формат.

Ключевые события:

- `payment_create_requested`;
- `payment_created`;
- `payment_idempotency_replay`;
- `payment_idempotency_conflict`;
- `outbox_event_created`;
- `outbox_publish_started`;
- `outbox_event_published`;
- `outbox_publish_failed`;
- `payment_message_received`;
- `payment_processing_started`;
- `payment_succeeded`;
- `payment_failed`;
- `payment_processing_noop`;
- `webhook_delivery_started`;
- `webhook_delivered`;
- `webhook_delivery_failed`;
- `message_retry_scheduled`;
- `message_sent_to_dlq`;
- `api_auth_failed`.

Секреты, включая `X-API-Key`, не логируются.

[К оглавлению](#оглавление)

## Какие технические решения и компромиссы приняты?

- `Idempotency-Key` обязателен, чтобы клиент явно управлял HTTP-идемпотентностью.
- Тесты по умолчанию не требуют Docker.
- SQLite для тестов не используется; PostgreSQL-specific поведение проверяется integration tests с
  marker `integration`.
- PostgreSQL UUID использует встроенный `gen_random_uuid()` без включения `pgcrypto`.
- API не публикует события напрямую в RabbitMQ.
- Outbox publisher помечает row как `published` только после успешной публикации.
- Delivery model — at-least-once.
- Retry broker-based через TTL/DLX.
- DLQ redrive не автоматизирован.
- Exactly-once webhook не гарантируется.

[К оглавлению](#оглавление)

## Что можно улучшить для production?

- Очистка, архивирование или партиционирование outbox.
- Метрики и tracing для outbox lag, retry count и DLQ size.
- Secret manager для API keys и credentials.
- Подпись webhook payload.
- Автоматический DLQ redrive с audit log.
- Rate limits.
- Schema versioning для событий.
- Более полный набор integration, load и chaos tests.
- Отдельный статус `processing` или lease-механизм для долгих gateway calls.

Более зрелый вариант outbox relay может уменьшить время удержания locks и blast radius дублей через
state machine:

```text
pending -> publishing -> published
```

Типичные дополнительные поля:

- `locked_by`;
- `locked_until`;
- `available_at`;
- `next_attempt_at`;
- `last_error`;
- `attempts`.

Тогда flow становится таким:

```text
BEGIN
  SELECT one/few pending rows FOR UPDATE SKIP LOCKED
  mark them publishing with a short lease
COMMIT

publish outside the database transaction

BEGIN
  if confirmed: mark published
  if failed: mark pending with error and next_attempt_at
COMMIT
```

Отдельный recovery job возвращает просроченные `publishing` rows обратно в `pending`.

Более простой промежуточный вариант — обрабатывать максимум одно событие на transaction внутри batch
loop:

```text
repeat up to OUTBOX_BATCH_SIZE:
  lock one row
  publish
  mark result
  commit
```

Это уменьшает последствия падения в конце batch, но увеличивает количество round-trips к базе.

[К оглавлению](#оглавление)

## Как запустить локальную разработку?

Установить зависимости:

```bash
uv sync
```

Запустить API локально:

```bash
uv run uvicorn app.main:app --reload
```

Применить миграции:

```bash
uv run alembic upgrade head
```

Запустить outbox publisher:

```bash
uv run python -m app.infrastructure.messaging.outbox_worker
```

Запустить consumer:

```bash
uv run faststream run app.infrastructure.messaging.consumer:app
```

Makefile содержит удобные цели:

```bash
make test
make lint
make migrate
make api
make publisher
make consumer
```

На Windows `make` может быть не установлен. В таком случае используйте прямые `uv run ...` команды.

[К оглавлению](#оглавление)

## Как запустить тесты и линтер?

Быстрые тесты без Docker:

```bash
uv run pytest
```

Ruff:

```bash
uv run ruff check .
```

Integration tests требуют PostgreSQL. Пример с PostgreSQL из Docker Compose:

```bash
docker compose exec postgres createdb -U postgres payments_test
```

Bash:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/payments_test \
  uv run pytest -m integration
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/payments_test'
uv run pytest -m integration
```

[К оглавлению](#оглавление)

## Где лежат ключевые файлы?

| Путь | Назначение |
| --- | --- |
| `app/main.py` | FastAPI application |
| `app/core/config.py` | Pydantic Settings |
| `app/domain/payment.py` | Доменная модель payment |
| `app/application/services.py` | Use cases |
| `app/infrastructure/db/models.py` | SQLAlchemy models |
| `app/infrastructure/db/repositories.py` | SQLAlchemy repositories |
| `app/infrastructure/db/uow.py` | Unit of Work |
| `app/infrastructure/messaging/topology.py` | RabbitMQ topology |
| `app/infrastructure/messaging/outbox_worker.py` | Outbox publisher entrypoint |
| `app/infrastructure/messaging/consumer.py` | FastStream consumer |
| `app/webhook_echo.py` | Dev echo-service для webhook-запросов |
| `alembic/versions/0001_create_payments_and_outbox.py` | Первая миграция |
| `docker-compose.yml` | Docker Compose stack |
| `.env.example` | Пример env-настроек |
| `tests/` | Тесты |

[К оглавлению](#оглавление)
