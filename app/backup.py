"""Create a consistent SQLite backup; copy it off the VM separately."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import settings
from .db import DB_PATH, init_db


def main():
    init_db()
    folder = settings.data_dir / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"work-engine-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.sqlite3"
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    print(target)


if __name__ == "__main__":
    main()
