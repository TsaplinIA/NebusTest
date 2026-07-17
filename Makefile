.PHONY: dev up down test lint migrate api publisher consumer logs

dev: up

up:
	docker compose up --build

down:
	docker compose down

test:
	uv run pytest

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

api:
	uv run uvicorn app.main:app --reload

publisher:
	uv run python -m app.infrastructure.messaging.outbox_worker

consumer:
	uv run faststream run app.infrastructure.messaging.consumer:app

logs:
	docker compose logs -f
