import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from event_inbox.app import create_app
from event_inbox.config import Settings

KEY = "synthetic-test-key-with-at-least-32-characters"
HEADERS = {"X-API-Key": KEY}
EVENT = {
    "event_id": "a8dc19e4-b341-4d73-9ade-521a3069fe01",
    "event_type": "enrollment.created",
    "schema_version": 1,
    "occurred_at": "2026-10-26T14:00:00Z",
    "data": {
        "student_id": "STU-DEMO-0001",
        "course_id": "CRS-DEMO-0101",
        "term": "2026-FALL",
    },
}


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key=KEY, database_path=tmp_path / "inbox.sqlite3", busy_timeout_ms=100)


@contextmanager
def client_for(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client(settings):
    with client_for(settings) as client:
        yield client


def rows(settings):
    with sqlite3.connect(settings.database_path) as connection:
        return connection.execute("SELECT count(*) FROM events").fetchone()[0]


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    return body


def test_receive_persists_then_retrieve_after_restart(settings):
    with client_for(settings) as first:
        response = first.post("/v1/events", json=EVENT, headers=HEADERS)
        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["event_id"] == EVENT["event_id"]
        assert receipt["status"] == "received"
        assert receipt["source"] == "synthetic-registry"
        assert receipt["event"] == EVENT
        assert receipt["received_at"].endswith("Z")
        assert response.headers["Location"] == f"/v1/events/{EVENT['event_id']}"
    with client_for(settings) as second:
        found = second.get(response.headers["Location"], headers=HEADERS)
        assert found.status_code == 200
        assert found.json() == receipt
    assert rows(settings) == 1


def test_identical_retry_returns_same_receipt(client, settings):
    first = client.post("/v1/events", json=EVENT, headers=HEADERS)
    retry = client.post("/v1/events", json=EVENT, headers=HEADERS)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert rows(settings) == 1


def test_retry_ignores_json_order_whitespace_and_timezone_spelling(client, settings):
    first = client.post("/v1/events", json=EVENT, headers=HEADERS)
    reordered = dict(reversed(list(deepcopy(EVENT).items())))
    reordered["occurred_at"] = "2026-10-26T10:00:00-04:00"
    retry = client.post(
        "/v1/events",
        content=json.dumps(reordered, indent=4),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json()
    assert rows(settings) == 1


def test_same_id_with_changed_content_is_conflict(client, settings):
    first = client.post("/v1/events", json=EVENT, headers=HEADERS)
    changed = deepcopy(EVENT)
    changed["data"]["course_id"] = "CRS-DEMO-0202"
    response = client.post("/v1/events", json=changed, headers=HEADERS)
    assert_error(response, 409, "idempotency_conflict")
    assert client.get(first.headers["Location"], headers=HEADERS).json() == first.json()
    assert rows(settings) == 1


def test_different_event_ids_are_separate_events(client, settings):
    assert client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201
    another = {**EVENT, "event_id": str(uuid4())}
    assert client.post("/v1/events", json=another, headers=HEADERS).status_code == 201
    assert rows(settings) == 2


def test_parallel_retries_across_app_instances_commit_once(settings):
    with client_for(settings) as first, client_for(settings) as second:

        def send(index):
            return (first if index % 2 else second).post("/v1/events", json=EVENT, headers=HEADERS)

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(send, range(16)))
    assert sorted(r.status_code for r in responses) == [200] * 15 + [201]
    assert all(r.json() == responses[0].json() for r in responses)
    assert rows(settings) == 1


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": "é"}])
def test_unauthorized_write_is_rejected(client, settings, headers):
    # httpx only allows ASCII in string headers; bytes model an incoming Latin-1 header.
    if headers.get("X-API-Key") == "é":
        headers = {b"X-API-Key": b"\xe9"}
    response = client.post("/v1/events", json=EVENT, headers=headers)
    assert_error(response, 401, "unauthorized")
    assert rows(settings) == 0


def test_read_also_requires_authentication(client):
    response = client.get(f"/v1/events/{EVENT['event_id']}")
    assert_error(response, 401, "unauthorized")


@pytest.mark.parametrize(
    "path,value",
    [
        (("event_id",), "not-a-uuid"),
        (("event_type",), "enrollment.deleted"),
        (("schema_version",), 2),
        (("schema_version",), True),
        (("schema_version",), "1"),
        (("occurred_at",), "2026-10-26T14:00:00"),
        (("occurred_at",), 1793023200),
        (("occurred_at",), "0001-01-01T00:00:00+01:00"),
        (("occurred_at",), "9999-12-31T23:59:59-01:00"),
        (("data", "student_id"), "real-student-123"),
        (("data", "course_id"), ""),
        (("data", "term"), "2026-WHATEVER"),
        (("data", "student_id"), 123),
        (("extra",), "must-not-be-silently-dropped"),
        (("data", "name"), "should-not-accept-personal-data"),
    ],
)
def test_invalid_event_never_persists(client, settings, path, value):
    invalid = deepcopy(EVENT)
    target = invalid
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    response = client.post("/v1/events", json=invalid, headers=HEADERS)
    body = assert_error(response, 422, "validation_error")
    assert "input" not in json.dumps(body)
    assert rows(settings) == 0


@pytest.mark.parametrize("payload", [{}, [], None])
def test_missing_or_wrong_envelope(client, settings, payload):
    response = client.post(
        "/v1/events",
        content=json.dumps(payload),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert_error(response, 422, "validation_error")
    assert rows(settings) == 0


def test_malformed_json_has_safe_error(client, settings):
    response = client.post(
        "/v1/events",
        content='{"secret":BROKEN',
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert_error(response, 422, "validation_error")
    assert "BROKEN" not in response.text
    assert rows(settings) == 0


def test_large_chunked_body_is_rejected_without_write(client, settings):
    response = client.post(
        "/v1/events",
        content=iter([b"x" * 17000, b"x" * 17000]),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert_error(response, 413, "payload_too_large")
    assert rows(settings) == 0


def test_unknown_event_returns_safe_404(client):
    assert_error(client.get(f"/v1/events/{uuid4()}", headers=HEADERS), 404, "not_found")


def test_unknown_route_uses_error_envelope(client):
    assert_error(client.get("/missing"), 404, "not_found")


def test_readiness_checks_database_and_liveness_does_not(client, settings):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    settings.database_path.unlink()
    assert client.get("/health/live").status_code == 200
    assert_error(client.get("/health/ready"), 503, "storage_unavailable")


def test_busy_database_returns_retryable_error_and_can_retry(client, settings):
    with sqlite3.connect(settings.database_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        response = client.post("/v1/events", json=EVENT, headers=HEADERS)
        assert_error(response, 503, "storage_unavailable")
        assert response.headers["Retry-After"] == "1"
        lock.rollback()
    assert client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201
    assert rows(settings) == 1


def test_failed_insert_is_not_acknowledged_or_partially_saved(client, settings):
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON events
                              BEGIN SELECT RAISE(ABORT, 'private database detail'); END""")
    response = client.post("/v1/events", json=EVENT, headers=HEADERS)
    assert_error(response, 503, "storage_unavailable")
    assert "private database detail" not in response.text
    assert rows(settings) == 0
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TRIGGER fail_insert")
    assert client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201


def test_request_ids_are_generated_and_do_not_trust_client_input(client):
    first = client.get("/health/live", headers={"X-Request-ID": "untrusted-client-value"})
    second = client.get("/health/live")
    assert first.headers["X-Request-ID"] != "untrusted-client-value"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_openapi_describes_auth_replay_and_errors(client):
    schema = client.get("/openapi.json").json()
    post = schema["paths"]["/v1/events"]["post"]
    assert post["security"] == [{"APIKeyHeader": []}]
    assert {"200", "201", "401", "409", "413", "422", "503"} <= set(post["responses"])
    assert post["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorResponse"
    )
    assert schema["components"]["schemas"]["IncomingEvent"]["additionalProperties"] is False
    assert client.get("/docs").status_code == 200


def test_logs_have_outcome_and_request_id_but_no_secrets(client, caplog):
    with caplog.at_level("INFO", logger="event_inbox"):
        response = client.post("/v1/events", json=EVENT, headers=HEADERS)
    assert response.headers["X-Request-ID"] in caplog.text
    assert "event_received" in caplog.text
    assert KEY not in caplog.text
    assert "STU-DEMO-0001" not in caplog.text


@pytest.mark.parametrize("key", ["", "too-short"])
def test_configuration_rejects_weak_keys(tmp_path, key):
    with pytest.raises(ValueError):
        Settings(api_key=key, database_path=tmp_path / "inbox.db")


def test_failed_commit_is_rolled_back_before_acknowledgment(client, settings):
    # In rollback-journal mode a reader lets BEGIN IMMEDIATE and INSERT succeed,
    # but prevents the writer from committing. This tests failure at COMMIT.
    with sqlite3.connect(settings.database_path) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM events").fetchall()
        response = client.post("/v1/events", json=EVENT, headers=HEADERS)
        assert_error(response, 503, "storage_unavailable")
        reader.rollback()
    assert rows(settings) == 0
    assert client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201


def test_corrupted_record_returns_safe_internal_error(client, settings):
    assert client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE events SET payload_json = ?", ('{"private":"internal-value"}',))
    response = client.get(f"/v1/events/{EVENT['event_id']}", headers=HEADERS)
    assert_error(response, 500, "internal_error")
    assert "internal-value" not in response.text


def test_unexpected_error_does_not_escape_to_server_logs(settings):
    with TestClient(create_app(settings), raise_server_exceptions=True) as strict_client:
        assert strict_client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE events SET payload_json = ?", ('{"private":"do-not-log-this"}',)
            )
        # An exception escaping the ASGI app is logged by Uvicorn with its raw
        # details, even if Starlette already sent a sanitized 500 response.
        response = strict_client.get(f"/v1/events/{EVENT['event_id']}", headers=HEADERS)
        assert_error(response, 500, "internal_error")
        assert "do-not-log-this" not in response.text


def test_missing_key_from_environment_fails_closed(monkeypatch):
    monkeypatch.delenv("INBOX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="INBOX_API_KEY"):
        create_app()


def test_config_can_be_loaded_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_API_KEY", KEY)
    monkeypatch.setenv("INBOX_DATABASE_PATH", str(tmp_path / "env.sqlite3"))
    with TestClient(create_app()) as env_client:
        assert env_client.post("/v1/events", json=EVENT, headers=HEADERS).status_code == 201


def test_unknown_schema_version_refuses_startup(settings):
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="schema version"), client_for(settings):
        pass


def test_parallel_conflicting_payloads_preserve_winning_event(client, settings):
    changed = deepcopy(EVENT)
    changed["data"]["course_id"] = "CRS-DEMO-0202"
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda event: client.post("/v1/events", json=event, headers=HEADERS),
                [EVENT, changed],
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    winner = next(r for r in responses if r.status_code == 201)
    assert client.get(winner.headers["Location"], headers=HEADERS).json() == winner.json()
    assert rows(settings) == 1
