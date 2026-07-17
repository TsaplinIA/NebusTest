from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.presentation.api.v1.payments import router as payments_router
from app.presentation.dependencies import verify_api_key
from app.presentation.exception_handlers import register_exception_handlers

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(
    title="Payments Processing Service",
    version="0.1.0",
)
register_exception_handlers(app)
app.include_router(payments_router)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            parameters = operation.get("parameters", [])
            for parameter in parameters:
                if (
                    parameter.get("in") == "header"
                    and parameter.get("name") in {"X-API-Key", "Idempotency-Key"}
                ):
                    parameter["required"] = True

    payment_create = schema.get("components", {}).get("schemas", {}).get("PaymentCreate")
    if payment_create is not None:
        webhook_url = payment_create.get("properties", {}).get("webhook_url")
        if webhook_url is not None:
            webhook_url["default"] = settings.swagger_default_webhook_url
            webhook_url["example"] = settings.swagger_default_webhook_url

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health", dependencies=[Depends(verify_api_key)], include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
