import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    database_path: Path
    busy_timeout_ms: int = 1000

    def __post_init__(self):
        if len(self.api_key) < 32 or not self.api_key.isascii() or self.api_key.isspace():
            raise ValueError("INBOX_API_KEY must contain at least 32 ASCII characters")
        if not 1 <= self.busy_timeout_ms <= 5000:
            raise ValueError("busy_timeout_ms must be between 1 and 5000")
        if str(self.database_path) == ":memory:":
            raise ValueError("Use a file-backed SQLite database")

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.environ.get("INBOX_API_KEY", ""),
            database_path=Path(os.environ.get("INBOX_DATABASE_PATH", "data/inbox.sqlite3")),
        )
