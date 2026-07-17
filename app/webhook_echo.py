import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Webhook Echo Service",
    version="0.1.0",
)


def decode_body(body: bytes) -> Any:
    if not body:
        return None

    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def normalize_headers(request: Request) -> dict[str, str]:
    return {key: value for key, value in request.headers.items()}


def normalize_query_params(request: Request) -> dict[str, list[str]]:
    return {
        key: request.query_params.getlist(key)
        for key in request.query_params.keys()
    }


async def build_echo_payload(request: Request, path: str) -> dict[str, Any]:
    body = await request.body()
    return {
        "method": request.method,
        "url": str(request.url),
        "base_url": str(request.base_url),
        "path": request.url.path,
        "path_param": path,
        "query_params": normalize_query_params(request),
        "headers": normalize_headers(request),
        "client": request.client.host if request.client else None,
        "body": decode_body(body),
        "body_size": len(body),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "webhook-echo"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def echo(request: Request, path: str) -> JSONResponse:
    payload = await build_echo_payload(request, path)
    logger.info(
        "webhook_echo_request_received",
        extra={"event": "webhook_echo_request_received", **payload},
    )
    return JSONResponse({"received": True, "request": payload})
