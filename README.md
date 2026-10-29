# Integration Event Inbox

[![Backend checks](https://github.com/Jhoan-JS/integration-event-inbox/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Jhoan-JS/integration-event-inbox/actions/workflows/ci.yml)

A FastAPI service for receiving enrollment events from a fictional academic
registry. It validates each event, stores it in SQLite and returns a receipt.
Sending the same event again returns the original receipt without another write.

The problem is a common one in integrations: a producer times out and cannot tell
whether its request succeeded. Retrying should be safe, but reusing an event ID
with different data should be caught rather than silently accepted.

This is a standalone project with synthetic data. It has no dependency on
PeopleSoft, Moodle or any client system. A receipt means **stored in the inbox**;
downstream enrollment processing is outside the scope of this service.

## Run locally

Install **Python 3.12** and [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/Jhoan-JS/integration-event-inbox.git
cd integration-event-inbox
uv sync --locked
```

Linux / macOS:

```bash
export INBOX_API_KEY="$(uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export INBOX_DATABASE_PATH="data/inbox.sqlite3"
uv run --locked uvicorn event_inbox.app:create_app --factory --host 127.0.0.1 --port 8000 --log-config logging.json
```

Windows PowerShell:

```powershell
$env:INBOX_API_KEY = uv run --locked python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:INBOX_DATABASE_PATH = "data/inbox.sqlite3"
uv run --locked uvicorn event_inbox.app:create_app --factory --host 127.0.0.1 --port 8000 --log-config logging.json
```

Open [Swagger UI](http://127.0.0.1:8000/docs) and use **Authorize** to enter the key.
The [OpenAPI schema](docs/openapi.json) is also available at `/openapi.json`.
Swagger loads its UI assets from a CDN; the API itself needs no external services.

| Variable | Purpose | Default |
| --- | --- | --- |
| `INBOX_API_KEY` | Shared key for the single producer; at least 32 ASCII characters | Required |
| `INBOX_DATABASE_PATH` | File-backed SQLite database | `data/inbox.sqlite3` |

Configuration comes from environment variables; `.env` files are not loaded.
Startup creates the database directory and schema. A missing key prevents startup.

## Send an event, then retry it

The [sample event](examples/enrollment.created.json) uses made-up student and
course IDs:

```json
{
  "event_id": "a8dc19e4-b341-4d73-9ade-521a3069fe01",
  "event_type": "enrollment.created",
  "schema_version": 1,
  "occurred_at": "2026-10-26T14:00:00Z",
  "data": {
    "student_id": "STU-DEMO-0001",
    "course_id": "CRS-DEMO-0101",
    "term": "2026-FALL"
  }
}
```

In another terminal, set `INBOX_API_KEY` to the same key used by the server:

```bash
curl -i http://127.0.0.1:8000/v1/events \
  -H "X-API-Key: $INBOX_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/enrollment.created.json
```

For PowerShell:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8000/v1/events -Method Post -Headers @{"X-API-Key"=$env:INBOX_API_KEY} -ContentType "application/json" -InFile examples/enrollment.created.json
```

Try these three cases:

1. **New event:** returns `201 Created`, a receipt and a `Location` header.
2. **Same event again:** returns `200 OK` with `Idempotent-Replayed: true` and
   exactly the same receipt, including its original `received_at`.
3. **Same ID, different course:** change `course_id` to `CRS-DEMO-0202` and resend.
   Returns `409 Conflict`; the first event stays unchanged.

Restarting the server preserves the receipt. If the sample was already sent in
an earlier session, its first response will be a replay; use a new UUID to try
another first delivery. The [HTTP smoke test](scripts/smoke_test.py) runs these
cases automatically with a temporary database.

## API contract

| Endpoint | Authentication | Response |
| --- | --- | --- |
| `POST /v1/events` | `X-API-Key` | `201` new event, `200` identical retry |
| `GET /v1/events/{event_id}` | `X-API-Key` | `200` stored receipt, `404` unknown ID |
| `GET /health/live` | None | `200` while the process responds |
| `GET /health/ready` | None | `200` with a usable event table, otherwise `503` |

Requests must use schema version `1`, the `enrollment.created` event type and a
timestamp string with a timezone. Unknown fields are rejected. Bodies are limited
to 32 KiB, including chunked requests.

| Error | Meaning |
| --- | --- |
| `401 unauthorized` | Missing or incorrect API key |
| `409 idempotency_conflict` | An existing event ID was reused with changed content |
| `413 payload_too_large` | Body exceeds 32,768 bytes |
| `422 validation_error` | Malformed JSON or invalid event fields |
| `500 internal_error` | Unexpected failure; use the request ID to investigate |
| `503 storage_unavailable` | Database unavailable, locked or unable to commit |

Errors share an envelope containing `code`, `message`, `request_id` and validation
`issues`. Responses also include `X-Request-ID`. Logs contain outcomes and request
IDs without keys or event payloads. Storage errors include `Retry-After: 1`;
producers should retry the same event with bounded backoff and alert on persistent
failure.

## Tests

```bash
uv run --locked pytest --cov=event_inbox --cov-report=term-missing
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/export_openapi.py --check
uv run --locked python scripts/smoke_test.py
uv build
```

The 47 pytest cases use real file-backed SQLite databases. They cover concurrent
retries through two app instances, conflicting payloads, restart persistence,
validation, authentication, locked storage and rollback when `COMMIT` fails.
The smoke test starts Uvicorn, sends real HTTP requests, restarts it and checks
that unexpected errors do not leak stored values into server logs.

[GitHub Actions](https://github.com/Jhoan-JS/integration-event-inbox/actions/workflows/ci.yml)
runs the suite, smoke test, lint, OpenAPI comparison and package build on Linux
and Windows with Python 3.12. Python 3.13 and 3.14 are allowed by the package
metadata but have not been tested. To update the OpenAPI snapshot, run
`uv run --locked python scripts/export_openapi.py`.

## Design choices and limits

SQLite keeps the project runnable without containers or hosted accounts. Event
identity is enforced by a primary key on `(source, event_id)` and a short
transaction acquired with `BEGIN IMMEDIATE`. The receipt only leaves the storage
layer after commit. Timestamp normalization and canonical JSON make equivalent
requests compare consistently. See [the implementation notes](docs/architecture.md)
for the transaction flow and failure cases.

The service has one producer and one shared key. It has no queue, worker, external
adapter or business-level enrollment uniqueness check: different event IDs are
different messages. A production integration would also need producer-specific
credentials, TLS, ingress limits, backups, migrations and an explicit downstream
processing model. The SQLite file belongs on local persistent storage; PostgreSQL
would be a better fit for multiple hosts or sustained write contention.
