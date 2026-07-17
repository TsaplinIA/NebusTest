from app.core.config import Settings


def test_settings_builds_default_urls_from_components() -> None:
    settings = Settings(_env_file=None)

    assert (
        settings.database_url
        == "postgresql+asyncpg://postgres:postgres@postgres:5432/payments"
    )
    assert settings.rabbitmq_url == "amqp://guest:guest@rabbitmq:5672/"


def test_settings_builds_database_url_from_postgres_components() -> None:
    settings = Settings(
        _env_file=None,
        POSTGRES_HOST="db",
        POSTGRES_PORT=15432,
        POSTGRES_DB="payments_test",
        POSTGRES_USER="user",
        POSTGRES_PASSWORD="password",
    )

    assert (
        settings.database_url
        == "postgresql+asyncpg://user:password@db:15432/payments_test"
    )


def test_explicit_database_url_overrides_components() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://override:override@host:5432/db",
        POSTGRES_DB="ignored",
    )

    assert settings.database_url == "postgresql+asyncpg://override:override@host:5432/db"
