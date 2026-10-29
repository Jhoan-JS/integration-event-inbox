# Verification record

Date: **2026-10-29**. Environment: Linux, CPython **3.12.14**, uv **0.13.4**.
This records observed local results, not a hosted CI result or deployment claim.

## Repository inspection

The GitHub account inventory and these public sources were inspected before
choosing the new project:

- [Profile README](https://github.com/Jhoan-JS/jhoan-js/blob/master/README.md).
- [WhatsApp demonstration](https://github.com/Jhoan-JS/whatsapp-ai-receptionist-demo):
  its README and static-export configuration identify a frontend-only prototype.
- [Natours](https://github.com/Jhoan-JS/Natours-API): README identifies course origin;
  package.json contains a placeholder failing test script.
- [RestApi_Nodejs](https://github.com/Jhoan-JS/RestApi_Nodejs): package.json likewise
  contains a placeholder test script; repository inventory shows its small Express API.
- [API-STORE](https://github.com/Jhoan-JS/API-STORE): README describes product CRUD
  and login/roles, a different problem from event ingestion and durable receipts.

This was a targeted overlap review, not a complete security or quality audit of
those repositories. No private project implementation was used as source.

## Executed checks

| Check | Observed result |
| --- | --- |
| `uv sync --locked` | Success with the committed lock file |
| `uv run --locked pytest --cov=event_inbox --cov-report=term-missing` | **47 passed**, **98% statement coverage** of application package |
| `uv run --locked ruff check .` | Pass |
| `uv run --locked ruff format --check .` | Pass |
| `uv run --locked python scripts/export_openapi.py --check` | Snapshot matches the application |
| `uv run --locked python scripts/smoke_test.py` | Pass over real localhost HTTP with Uvicorn subprocesses |
| `uv build` | Source distribution and wheel built successfully |
| Install wheel into a separate environment, run HTTP smoke test | Pass with only runtime dependencies installed |
| Extract source without `.venv` and caches; sync and run tests/smoke | Pass; repeated for the final delivery |
| `git diff --check` | Pass |

## Material behavior covered

- New receipt after successful commit; same receipt on an identical retry.
- Lookup and deduplication after closing and reopening the application/database.
- Sixteen concurrent deliveries through two independent app instances, one row.
- Concurrent reuse of an event ID with changed content, one winner and one 409.
- Equivalent UTC timestamps and reordered JSON map to identical validated content.
- Different event IDs remain distinct messages, even with matching business data.
- Locked database, trigger-induced insert failure and a reader blocking COMMIT
  produce 503 without false acknowledgment; retries then succeed.
- Authentication applies to writes and reads; missing/weak configuration fails closed.
- Invalid envelope, unknown fields, synthetic ID formats, unsupported versions,
  naive/numeric timestamps and timestamps outside UTC range return safe 422 errors.
- Streamed HTTP request larger than 32 KiB returns 413.
- Corrupt stored content returns a safe 500 without exposing the raw value in
  Uvicorn logs. The smoke test uses the documented logging configuration and
  checks actual server output for a sentinel payload, a key and a student ID.
- Health readiness exercises the real event table and does not recreate a missing DB.
- OpenAPI documents authentication, request/response models and custom errors.

## Independent review and corrections

A read-only reviewer inspected the initial implementation and independently ran
44 tests and the OpenAPI comparison. It found:

1. **Raw exception detail could reach Uvicorn logs after a sanitized 500.**
   Reproduced in a regression before fixing; unexpected errors are now handled
   within the request boundary before Starlette can re-raise them to the server.
   The full suite and actual server-log smoke check pass after the change.
2. **Extreme timezone normalization could overflow and return 500.** Reproduced
   with two boundary timestamps, then converted into validation errors. Both
   regression cases now return 422 without persistence.

No review finding remains deferred. The reviewer did not perform a second review
of the fixes; their regression tests and the full suite verified the corrections.

## Remote verification

On 2026-10-29, the owner authorized creation and upload to the private GitHub
repository. Repository creation, implementation upload and private visibility
were confirmed. The first hosted run passed all checks on Linux and all 47 pytest
tests on Windows. Its Windows smoke test exposed an unclosed SQLite connection
in the test script's cleanup; the connection now closes explicitly before the
temporary directory is removed. A fresh cross-platform CI run will verify the fix.

## Unverified boundaries

- The final cross-platform hosted result is pending after the Windows smoke
  cleanup fix. No unrelated repository was repurposed.
- Python 3.13/3.14 execution is unverified; CI uses Python 3.12.
- Production load, multiple hosts, external systems and external side effects:
  intentionally outside this demo's scope. No external integration is claimed.

The project remains unpublished. Public visibility requires separate owner approval.
