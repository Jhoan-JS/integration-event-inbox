# Implementation notes

## The boundary

The service receives `enrollment.created` messages from one fictional registry.
It stores messages for later retrieval. It does not create an enrollment in a
second system or tell the producer that downstream work is complete.

That distinction matters when the producer loses a response. If the commit
succeeded but the response never arrived, the next request must recover the
original receipt. If the commit failed, the next request must be able to insert
the event normally.

## Request flow

`RequestBoundary` assigns a request ID and reads at most 32 KiB before forwarding
the body. The limit counts actual bytes, not the supplied `Content-Length`.
FastAPI authenticates the API key and validates `IncomingEvent` before calling
the store. Validation errors expose field locations and error types, not input
values.

`EventStore` opens a separate SQLite connection for each operation. Blocking
database work runs in synchronous endpoints, which FastAPI executes in its
thread pool. The application initializes the schema during lifespan startup.
After startup, connections use `mode=rw` so a missing database does not silently
become an empty file.

## Storage and identity

The table is deliberately small:

```sql
CREATE TABLE events (
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (source, event_id)
);
```

The server assigns `synthetic-registry` as the source. There is no client-supplied
source field and no separate idempotency header. A producer must keep its event
ID when retrying; generating another UUID creates another message.

Before comparison, the store serializes the validated event with sorted keys and
fixed JSON separators. UUIDs use their normalized representation and timestamps
are converted to UTC. JSON whitespace, field order, UUID case and equivalent
timezone spellings therefore do not create conflicts. Student and course IDs
remain case-sensitive.

The reception timestamp is `received_at`, set once by the server. `occurred_at`
is the producer's event time. Neither field is used as an ordering or business
uniqueness rule.

## Receive transaction

1. Open a connection and execute `BEGIN IMMEDIATE`.
2. Look up `(source, event_id)` while holding the writer lock.
3. If found, compare its stored canonical JSON. Matching content returns the
   original receipt; changed content raises `IdempotencyConflict`.
4. If absent, insert the canonical event and its reception timestamp.
5. Exit the transaction successfully before returning a receipt to the route.

The database lock coordinates independent app instances; there is no process-local
deduplication state. The primary key also enforces uniqueness in the database.
SQLite permits one writer at a time, so this approach favors a simple transaction
over high write throughput. There is a one-second busy timeout.
The default rollback journal and synchronous durability settings are retained.

Deleting a row or replacing the database would remove its deduplication history.
The API provides no deletion endpoint or expiry policy.

## Failures worth testing

The tests exercise both acquisition and commit failures. A competing writer can
prevent `BEGIN IMMEDIATE`. An active reader can let the insert run but prevent
its `COMMIT`. Both cases must return `503`, leave no newly committed event and
allow a later retry. An insert failure is also tested with a SQLite trigger.

A conflict is a producer error, so it returns `409` rather than being retried by
the server. Schema errors return `422`. A storage failure returns `503` with
`Retry-After: 1`; this is a retry hint, not a guarantee that the failure will clear.

Unexpected exceptions are caught inside the request boundary before Starlette's
outer error middleware can re-raise them to Uvicorn. Otherwise, a sanitized HTTP
response could still leave raw stored values in a traceback. The smoke test
corrupts a temporary stored record and inspects actual server output for this
case. These endpoints do not stream responses; an error after response headers
have been sent cannot be replaced with another HTTP response.

## What a downstream consumer would change

Adding a worker would introduce a second state: received versus processed.
The receipt alone cannot establish that a remote system applied an event.
A consumer would need durable processing state, retry limits and a strategy for
idempotent effects in that destination. An outbox may be appropriate if producing
new messages must be atomic with a database change. Those concerns are left out
here rather than simulated with an in-memory background task.

The current schema uses `PRAGMA user_version = 1` and refuses unknown versions.
Further schema changes need explicit migrations. Multiple hosts, higher write
volume and real producer isolation would also require revisiting the database,
authentication and operating model.
