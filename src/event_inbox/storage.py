"""The database owns idempotency; every request uses its own connection."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import UUID

from event_inbox.config import Settings
from event_inbox.models import EventReceipt, IncomingEvent

SOURCE = "synthetic-registry"


class IdempotencyConflict(Exception):
    pass


class EventStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self, *, initialize=False):
        # mode=rw prevents a missing database from silently becoming an empty file.
        path = self.settings.database_path.resolve().as_uri()
        connection = sqlite3.connect(
            path + ("?mode=rwc" if initialize else "?mode=rw"),
            uri=True,
            timeout=self.settings.busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect(initialize=True)) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database schema version")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (source, event_id)
                )
            """)
            connection.execute("PRAGMA user_version = 1")

    def ready(self):
        with closing(self.connect()) as connection:
            # Exercise the actual table, rather than SELECT 1 on an empty database.
            connection.execute("SELECT event_id FROM events LIMIT 1").fetchone()

    @staticmethod
    def receipt(row) -> EventReceipt:
        return EventReceipt(
            event_id=row["event_id"],
            received_at=row["received_at"],
            event=IncomingEvent.model_validate_json(row["payload_json"]),
        )

    def receive(self, event: IncomingEvent) -> tuple[EventReceipt, bool]:
        canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with closing(self.connect()) as connection, connection:
            # Acquire the writer before checking the identity. All instances share
            # the same database lock; the primary key is a second integrity guard.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM events WHERE source = ? AND event_id = ?",
                (SOURCE, str(event.event_id)),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != canonical:
                    raise IdempotencyConflict
                receipt, replayed = self.receipt(existing), True
            else:
                received_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                connection.execute(
                    "INSERT INTO events (source, event_id, payload_json, received_at) "
                    "VALUES (?,?,?,?)",
                    (SOURCE, str(event.event_id), canonical, received_at),
                )
                receipt = EventReceipt(
                    event_id=event.event_id,
                    received_at=received_at,
                    event=event,
                )
                replayed = False
        # The context has committed successfully before a receipt can escape.
        return receipt, replayed

    def get(self, event_id: UUID) -> EventReceipt | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE source = ? AND event_id = ?",
                (SOURCE, str(event_id)),
            ).fetchone()
            return self.receipt(row) if row else None
