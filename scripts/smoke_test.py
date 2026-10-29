"""Run the actual packaged server, exercise HTTP, then verify a restart."""

import http.client
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def request(base, path, *, key=None, payload=None):
    headers = {"X-API-Key": key} if key else {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=body, headers=headers)
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, dict(response.headers), json.load(response)


@contextmanager
def running_server(port, environment, logs):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "event_inbox.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
                "--log-config",
                str(ROOT / "logging.json"),
            ],
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=output,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output.seek(0)
                    raise RuntimeError("Server exited before readiness: " + output.read())
                try:
                    if request(base, "/health/ready")[0] == 200:
                        break
                except (URLError, OSError):
                    pass
                Event().wait(0.05)
            else:
                raise RuntimeError("Server did not become ready within 15 seconds")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            output.seek(0)
            logs.append(output.read())


def main():
    event = json.loads((ROOT / "examples" / "enrollment.created.json").read_text())
    key = secrets.token_urlsafe(32)
    logs = []
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="inbox-smoke-") as directory:
        environment = {
            **os.environ,
            "INBOX_API_KEY": key,
            "INBOX_DATABASE_PATH": str(Path(directory) / "inbox.sqlite3"),
        }
        with running_server(port, environment, logs) as base:
            assert request(base, "/v1/events", payload=event)[0] == 401
            status, headers, receipt = request(base, "/v1/events", key=key, payload=event)
            assert status == 201, (status, receipt)
            location = headers["location"]
            status, headers, replay = request(base, "/v1/events", key=key, payload=event)
            assert status == 200 and replay == receipt
            assert headers["idempotent-replayed"] == "true"
            changed = json.loads(json.dumps(event))
            changed["data"]["course_id"] = "CRS-DEMO-0202"
            assert request(base, "/v1/events", key=key, payload=changed)[0] == 409
            invalid = {**event, "schema_version": 2}
            assert request(base, "/v1/events", key=key, payload=invalid)[0] == 422
            assert request(base, location, key=key)[2] == receipt

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/v1/events",
                    body=iter([b"x" * 17000, b"x" * 17000]),
                    headers={"X-API-Key": key, "Content-Type": "application/json"},
                    encode_chunked=True,
                )
                response = connection.getresponse()
                assert response.status == 413
                assert json.loads(response.read())["error"]["code"] == "payload_too_large"
            finally:
                connection.close()

        with running_server(port, environment, logs) as base:
            assert request(base, location, key=key)[2] == receipt
            assert request(base, "/v1/events", key=key, payload=event)[0] == 200
            with (
                closing(sqlite3.connect(environment["INBOX_DATABASE_PATH"])) as connection,
                connection,
            ):
                connection.execute(
                    "UPDATE events SET payload_json = ?", ('{"private":"smoke-sensitive-marker"}',)
                )
            status, headers, error = request(base, location, key=key)
            assert status == 500 and error["error"]["code"] == "internal_error"
            assert error["error"]["request_id"] == headers["x-request-id"]

    server_logs = "\n".join(logs)
    assert "smoke-sensitive-marker" not in server_logs
    assert key not in server_logs
    assert "STU-DEMO-0001" not in server_logs
    assert '"event": "internal_error"' in server_logs

    print(
        "HTTP smoke test passed: 401, 201, replay 200, conflict 409, validation 422, "
        "chunked-body 413, lookup, persistence after process restart and safe 500 logs"
    )


if __name__ == "__main__":
    main()
