from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


async def test_health_with_valid_api_key() -> None:
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"X-API-Key": settings.api_key},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": settings.app_env}


async def test_health_without_api_key_is_rejected() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
