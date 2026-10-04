"""Safely store an Apify API token in this app's private .env file."""
from __future__ import annotations

import getpass
import os
from pathlib import Path


def save_token(path: Path, token: str) -> None:
    token = token.strip()
    if not token or any(char.isspace() for char in token):
        raise ValueError("Enter one complete Apify token without spaces.")
    lines = path.read_text().splitlines() if path.exists() else []
    replaced = False
    updated = []
    for line in lines:
        if line.startswith("APIFY_TOKEN="):
            if replaced:
                continue
            updated.append("APIFY_TOKEN=" + token)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append("APIFY_TOKEN=" + token)
    temporary = path.with_name(path.name + ".apify-new")
    temporary.write_text("\n".join(updated) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


if __name__ == "__main__":
    secret = getpass.getpass("Paste the Apify API token here (it will stay hidden): ")
    save_token(Path(__file__).resolve().parents[1] / ".env", secret)
    print("Apify token saved in the private app settings file.")
