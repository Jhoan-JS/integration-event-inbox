import secrets
import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException

from event_inbox.config import Settings
from event_inbox.http import RequestBoundary, error_response, log_outcome
from event_inbox.models import ErrorResponse, EventReceipt, IncomingEvent
from event_inbox.storage import EventStore, IdempotencyConflict

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
ERRORS = {status: {"model": ErrorResponse} for status in (401, 404, 409, 413, 422, 500, 503)}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = EventStore(settings)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(
        title="Integration Event Inbox",
        description=(
            "A durable inbox for synthetic enrollment events. A receipt confirms storage, "
            "not downstream processing. Authenticate with X-API-Key."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestBoundary)

    def authenticate(key: Annotated[str | None, Security(api_key_header)]):
        if key is None or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "unauthorized")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo submitted values or parser/validator context.
        issues = [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
        return error_response(
            422,
            "validation_error",
            "Request does not match the event schema",
            request.state.request_id,
            issues=issues,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code, message = {
            401: ("unauthorized", "A valid X-API-Key is required"),
            404: ("not_found", "Resource not found"),
            405: ("method_not_allowed", "Method not allowed"),
        }.get(exc.status_code, ("request_error", "Request rejected"))
        return error_response(
            exc.status_code, code, message, request.state.request_id, headers=exc.headers
        )

    @app.exception_handler(IdempotencyConflict)
    async def conflict(request: Request, exc: IdempotencyConflict):
        return error_response(
            409,
            "idempotency_conflict",
            "This event ID was already received with different content",
            request.state.request_id,
        )

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc: sqlite3.Error):
        log_outcome("storage_unavailable", request.state.request_id)
        return error_response(
            503,
            "storage_unavailable",
            "Storage unavailable; retry the same event",
            request.state.request_id,
            headers={"Retry-After": "1"},
        )

    @app.get("/health/live", tags=["Health"])
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["Health"], responses={503: ERRORS[503]})
    def ready():
        store.ready()
        return {"status": "ready"}

    @app.post(
        "/v1/events",
        status_code=201,
        response_model=EventReceipt,
        tags=["Events"],
        dependencies=[Depends(authenticate)],
        summary="Receive a synthetic enrollment event",
        description=(
            "201 after durable commit; 200 and Idempotent-Replayed: true for an identical "
            "retry; 409 for the same ID with changed content. Maximum body: 32768 bytes."
        ),
        responses={**ERRORS, 200: {"model": EventReceipt, "description": "Identical retry"}},
    )
    def receive_event(event: IncomingEvent, request: Request, response: Response):
        receipt, replayed = store.receive(event)
        response.status_code = 200 if replayed else 201
        response.headers["Location"] = f"/v1/events/{receipt.event_id}"
        response.headers["Idempotent-Replayed"] = str(replayed).lower()
        log_outcome("event_replayed" if replayed else "event_received", request.state.request_id)
        return receipt

    @app.get(
        "/v1/events/{event_id}",
        response_model=EventReceipt,
        tags=["Events"],
        dependencies=[Depends(authenticate)],
        responses=ERRORS,
        summary="Retrieve a durable event receipt",
    )
    def get_event(event_id: UUID):
        receipt = store.get(event_id)
        if receipt is None:
            raise HTTPException(404, "not_found")
        return receipt

    return app
