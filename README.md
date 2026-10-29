# Integration Event Inbox

[![Backend checks](https://github.com/Jhoan-JS/integration-event-inbox/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/Jhoan-JS/integration-event-inbox/actions/workflows/ci.yml)

A small backend demonstration of reliable event ingestion with **Python,
FastAPI and SQLite**. A fictional academic registry sends enrollment events;
the API validates them, commits them to durable storage, and makes retries safe.

This project focuses on a narrow integration boundary that can be inspected and
tested locally. All example data is synthetic. It contains no client code,
institutional data, private endpoints, or proprietary integration contracts.

## What this demonstrates

- A closed, versioned event schema with explicit validation and UTC timestamps.
- API key authentication on ingestion and receipt retrieval.
- Transactional persistence and database-enforced event identity.
- Idempotent retries across concurrent application instances and restarts.
- Explicit conflict, validation, authentication and storage errors.
- Bounded request bodies and structured logs with server-generated request IDs.
- Automated tests against real file-backed SQLite, including failure at commit.
- OpenAPI documentation, locked dependencies and a real HTTP smoke test.

**A successful response means the event was received durably.** This is an inbox,
not a complete synchronization platform. It does not send data to an external
student system, create an enrollment, or claim exactly-once delivery.

## Run locally

Requirements: **Python 3.12** and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The package allows Python 3.12–3.14. Python 3.12 is verified on Linux and Windows
in [GitHub Actions](https://github.com/Jhoan-JS/integration-event-inbox/actions/runs/40934068390).
No hosted accounts, containers or external database are needed.

Extract this project or clone the repository (GitHub access is required while private):

```bash
git clone https://github.com/Jhoan-JS/integration-event-inbox.git
cd integration-event-inbox
```

```bash
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

Configuration is read from the process environment; `.env` files are not loaded.
The key has no default and must contain at least 32 ASCII characters. Missing or
invalid configuration prevents startup. The database directory is created at
startup; subsequent requests never silently recreate a missing database.

- [Swagger UI](http://127.0.0.1:8000/docs): click **Authorize**, then supply the key.
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json).
- [Committed OpenAPI snapshot](docs/openapi.json).

The Swagger UI loads its assets from a CDN. The JSON contract and API endpoints
do not need that CDN.

## Try the integration contract

`examples/enrollment.created.json` contains:

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

From a second Linux / macOS terminal, set `INBOX_API_KEY` to the same key used by
the server, then run:

```bash
curl -i http://127.0.0.1:8000/v1/events \
  -H "X-API-Key: $INBOX_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/enrollment.created.json
```

On PowerShell, set the same key in that terminal and use:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8000/v1/events -Method Post -Headers @{"X-API-Key"=$env:INBOX_API_KEY} -ContentType "application/json" -InFile examples/enrollment.created.json
```

The first delivery returns **201 Created**, a `Location` header and a receipt.
Run it again: it returns **200 OK**, `Idempotent-Replayed: true` and the same
receipt, including the original `received_at`. If a previous run used this event
ID, the first call will already be a replay; use a new UUID for a new event.

```bash
curl -i http://127.0.0.1:8000/v1/events/a8dc19e4-b341-4d73-9ade-521a3069fe01 \
  -H "X-API-Key: $INBOX_API_KEY"
```

Change `course_id` to `CRS-DEMO-0202` while retaining the event ID: the response is
**409 Conflict** and the original record is unchanged. Change `schema_version`
to `2` or remove the timestamp timezone: the response is **422** and nothing is
written. The [smoke test](scripts/smoke_test.py) runs these scenarios automatically.

## API and error behavior

| Endpoint | Authentication | Behavior |
| --- | --- | --- |
| `POST /v1/events` | `X-API-Key` | 201 new receipt; 200 identical replay |
| `GET /v1/events/{event_id}` | `X-API-Key` | 200 stored receipt; 404 unknown ID |
| `GET /health/live` | None | Process responds; no database access |
| `GET /health/ready` | None | 200 usable event table; 503 storage unavailable |

| Status | Error code | Producer action |
| --- | --- | --- |
| 401 | `unauthorized` | Correct credentials; do not retry blindly |
| 404 | `not_found` | Check the resource ID or route |
| 409 | `idempotency_conflict` | Investigate ID reuse; preserve the original event |
| 413 | `payload_too_large` | Send a body no larger than 32,768 bytes |
| 422 | `validation_error` | Correct the schema or malformed JSON |
| 500 | `internal_error` | Investigate using the request ID; retry safely with the same ID |
| 503 | `storage_unavailable` | Retry the identical event with bounded backoff and jitter |

503 responses include `Retry-After: 1`. A permanent storage problem can also
produce 503; producers must cap retries and alert rather than retry forever.
Every API error uses this shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request does not match the event schema",
    "request_id": "f6608294-c49a-4913-8fe7-ed4a9278c78e",
    "issues": [{"location": ["body", "schema_version"], "type": "less_than_equal"}]
  }
}
```

Validation issues include locations and types, not submitted values. The same
request ID is returned in `X-Request-ID`. Client-supplied request IDs are replaced.
The supplied logging configuration emits JSON application logs without payloads,
API keys or raw database exception messages.

## Idempotency and transaction design

The server assigns the fixed source `synthetic-registry` from its single-producer
configuration. Clients cannot choose another source in the payload. Identity is
the database primary key **`(source, event_id)`**; no separate idempotency header
or in-memory cache is involved.

1. Authenticate and validate the event.
2. Normalize its timestamp to UTC and serialize validated content canonically.
3. Acquire a SQLite writer with `BEGIN IMMEDIATE`.
4. Insert a new identity, return the stored receipt for matching content, or
   reject changed content with 409.
5. Commit successfully before returning a receipt.

JSON field order, formatting, UUID case and equivalent timezone spellings do not
change identity or content. Data fields remain case-sensitive. Deduplication lasts
as long as the stored row exists; there is no expiry or deletion endpoint.
Different event IDs represent different messages, even with identical business
data. This is message deduplication, not business-level enrollment uniqueness.

SQLite serializes writers. Short transactions and a one-second busy timeout
bound contention; a busy or failed commit rolls back and returns 503. The tests
use independent app instances and real database locks to verify this behavior.
Receipt lookup is also authenticated. Full rationale: [design](docs/design.md).

## Verify

```bash
uv run --locked pytest --cov=event_inbox --cov-report=term-missing
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/smoke_test.py
uv build
```

The smoke test starts a real Uvicorn subprocess on localhost with a temporary
database and a random temporary key, checks the contract over HTTP, restarts the
server and verifies the original receipt. It leaves no demonstration records in
your normal database. It requires no API keys or hosted services from you.

The pytest suite covers validation, authentication, replay/conflict semantics,
restart persistence, concurrent retries, database lock contention, rollback at
commit, failed inserts, safe errors/logs, configuration and OpenAPI. GitHub Actions
runs the checks on Python 3.12 on Linux and Windows.
See [verification record](docs/verification.md) for checks actually run here.

Regenerate or check the OpenAPI snapshot:

```bash
uv run --locked python scripts/export_openapi.py
uv run --locked python scripts/export_openapi.py --check
```

## Scope and production tradeoffs

- This is a portfolio demonstration, not a production deployment template.
- One producer and one shared API key; no users, JWT, OAuth, tenant isolation,
  key rotation, revocation store or per-source authorization. Use TLS if requests
  leave localhost. Do not expose this demo directly to the internet.
- Local file-backed SQLite with its default rollback journal and full synchronous
  durability. Do not put the file on a network filesystem or ephemeral hosting
  disk. PostgreSQL is the next step for multiple hosts or higher write contention.
- No queue, worker, downstream adapter, delivery retries or outbox. An actual
  consumer would need an explicit processing state machine and its own idempotent
  effects. Network delivery cannot be assumed exactly once.
- No rate limiting, retention automation, metrics backend or backup scheduler.
  Request bodies are bounded; enforce connection timeouts and rate limits at an
  ingress before considering a deployment.
- Schema bootstrap uses `PRAGMA user_version = 1`; incompatible future schema
  versions are refused. A growing service needs versioned migrations and backups.
- Synthetic identifier patterns prevent accidental acceptance of common real
  identifiers; they do not establish that a caller's supplied data is fictional.

## Repository and publication

Repository: [Jhoan-JS/integration-event-inbox](https://github.com/Jhoan-JS/integration-event-inbox).
The project must stay **private until the owner approves publication**. Review
[the publication checklist](docs/publication.md) and CI results before any later
visibility change. A profile README change or public deployment requires a
separate decision. This project contains no command that makes a repository public.
