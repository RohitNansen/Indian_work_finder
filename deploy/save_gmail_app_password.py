"""Privately save a Google app password in the VM's existing .env file."""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def save_password(path: Path, value: str) -> None:
    password = "".join(value.split())
    if len(password) != 16 or not password.isascii() or not password.isalnum():
        raise ValueError("A Google app password has 16 letters or digits. Please try again.")
    if not path.is_file():
        raise FileNotFoundError(f"Private settings file not found: {path}")

    lines = path.read_text().splitlines()
    matches = [index for index, line in enumerate(lines)
               if line.startswith("GMAIL_APP_PASSWORD=")]
    if len(matches) > 1:
        raise ValueError("The settings file has more than one GMAIL_APP_PASSWORD line.")
    setting = f"GMAIL_APP_PASSWORD={password}"
    if matches:
        lines[matches[0]] = setting
    else:
        lines.append(setting)

    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.tmp-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as temporary:
            temporary.write("\n".join(lines) + "\n")
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        save_password(ENV_PATH, getpass.getpass("Paste Google app password (hidden): "))
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    print("Saved privately. Tell Codex the setup is complete; do not share the password.")
