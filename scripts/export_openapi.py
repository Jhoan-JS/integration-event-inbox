"""Generate an inspectable contract without reading credentials or creating a DB."""

import argparse
import json
from pathlib import Path

from event_inbox.app import create_app
from event_inbox.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = create_app(
        Settings(api_key="openapi-generation-only-not-a-live-key", database_path=Path("unused"))
    )
    rendered = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    target = ROOT / "docs" / "openapi.json"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != rendered:
            raise SystemExit("OpenAPI snapshot is stale; run scripts/export_openapi.py")
        print("OpenAPI snapshot matches the application")
    else:
        target.write_text(rendered, encoding="utf-8")
        print("Wrote docs/openapi.json")


if __name__ == "__main__":
    main()
