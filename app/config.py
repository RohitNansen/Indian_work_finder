from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Small .env reader so deployment does not need another dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data")).resolve()
    app_password: str = os.getenv("APP_PASSWORD", "")
    app_secret: str = os.getenv("APP_SECRET", "")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    jsearch_api_key: str = os.getenv("JSEARCH_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "google/gemini-3.8-flash")
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    quota_alert_email: str = os.getenv("QUOTA_ALERT_EMAIL", "")
    resend_daily_quota: int = int(os.getenv("RESEND_DAILY_QUOTA", "100"))
    resend_monthly_quota: int = int(os.getenv("RESEND_MONTHLY_QUOTA", "3000"))
    jsearch_max_requests_per_run: int = int(os.getenv("JSEARCH_MAX_REQUESTS_PER_RUN", "20"))
    openrouter_max_jobs_per_run: int = int(os.getenv("OPENROUTER_MAX_JOBS_PER_RUN", "40"))
    monthly_spend_limit_usd: float = float(os.getenv("MONTHLY_SPEND_LIMIT_USD", "8"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "uploads").mkdir(exist_ok=True)
(settings.data_dir / "exports").mkdir(exist_ok=True)
