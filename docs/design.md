# Durable event inbox: design and acceptance contract

This small demonstration receives synthetic `enrollment.created` events from a
fictional academic registry. It is independent of client code and private systems.

## Observed starting point

On 2026-10-29 the account repository inventory and the profile, WhatsApp demo,
Natours, RestApi_Nodejs and API-STORE documentation/configuration were reviewed.
The WhatsApp demo explicitly excludes a backend. The reviewed Node APIs focus on
CRUD, tours or products; both Natours and RestApi_Nodejs have placeholder test
commands. No equivalent public Python event inbox was found in that inspection.
Private repository contents were not copied or consulted.

## Decision

Build a synchronous durable inbox with FastAPI, Pydantic and Python's SQLite
driver. SQLite makes a clean checkout runnable without hosted accounts or Docker.
PostgreSQL would improve write concurrency but adds infrastructure to this small
exercise. A queue and worker are deferred because acknowledgment here means
durably **received**, never downstream processed. No enrollment is created in an
external system. Event IDs identify messages, not unique business enrollments.

## Contract and invariants

- One configured API key identifies one fixed source: `synthetic-registry`.
- Validate an explicit, closed, versioned envelope before opening a transaction.
- First delivery: 201 plus immutable receipt, only after a successful commit.
- Same source/event ID and canonical validated content: 200, same receipt.
- Same identity with changed content: 409, original row remains untouched.
- Normalize aware timestamps to UTC; JSON whitespace and field order do not matter.
- Database uniqueness and a short `BEGIN IMMEDIATE` transaction enforce deduplication
  across independent application instances; no in-memory lock or dedupe cache.
- Storage failure/lock contention: safe 503 with Retry-After, never a success receipt.
- Reads require the same authentication; unknown events return 404.
- Validation failures are 422, missing/incorrect credentials 401, oversized bodies
  413. Errors share a typed envelope with generated request ID.
- Streamed request bodies are bounded at 32 KiB, irrespective of Content-Length.
- No raw payloads, API keys or database exceptions in application logs/responses.
- Only synthetic student/course identifiers; unknown fields are rejected.

## Verification

Exercise real HTTP handlers with TestClient and real file-backed SQLite, including
restart persistence, parallel requests through two app instances, locked storage,
failed writes, retry conflicts, strict schemas, authentication, request size,
OpenAPI and safe logs. Also run the packaged application over a real localhost
socket and repeat a receipt lookup after restart. Generate an OpenAPI artifact,
locked dependencies and an English README, then verify a clean extracted bundle.

## Limits

One trusted producer, one local filesystem database, no delivery to external
systems, no worker, OAuth, JWT, distributed queue, external side effects or
exactly-once delivery claim. Schema bootstrap is version 1; future schema changes
need explicit migrations. Publication requires the owner's separate approval.
