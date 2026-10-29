"""Bound request bodies and correlate safe JSON errors and logs."""

import json
import logging
from time import monotonic
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("event_inbox")
MAX_BODY_BYTES = 32 * 1024


def error_response(status, code, message, request_id, *, issues=None, headers=None):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "issues": issues or [],
            }
        },
        headers={"X-Request-ID": request_id, **(headers or {})},
    )


def log_outcome(event, request_id, **fields):
    logger.info(json.dumps({"event": event, "request_id": request_id, **fields}, sort_keys=True))


class RequestBoundary:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = monotonic()
        response_started = False

        async def correlated_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode())]
                log_outcome(
                    "request_completed",
                    request_id,
                    status=message["status"],
                    duration_ms=round((monotonic() - started) * 1000, 2),
                )
            await send(message)

        if scope["method"] in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    response = error_response(
                        413, "payload_too_large", "Request body exceeds 32768 bytes", request_id
                    )
                    await response(scope, receive, correlated_send)
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break

            original_receive = receive
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = bounded_receive

        try:
            await self.app(scope, receive, correlated_send)
        except Exception as exc:
            # Starlette's outer ServerErrorMiddleware re-raises exceptions after
            # sending a 500. Catch before it so Uvicorn cannot log raw data from
            # exception messages (e.g. a corrupt stored document).
            if response_started:
                raise
            log_outcome("internal_error", request_id, exception_type=type(exc).__name__)
            response = error_response(500, "internal_error", "Unexpected server error", request_id)
            await response(scope, receive, correlated_send)
