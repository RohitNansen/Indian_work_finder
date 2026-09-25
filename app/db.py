from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

DB_PATH = settings.data_dir / "work_engine.sqlite3"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path | None = None):
    db = sqlite3.connect(path or DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL DEFAULT '',
  role_labels TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '',
  locations TEXT NOT NULL DEFAULT 'Chennai,Tamil Nadu,Bengaluru,Remote India',
  work_types TEXT NOT NULL DEFAULT 'Consulting,Part-time,Contract,Full-time',
  notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes (
  id INTEGER PRIMARY KEY, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
  file_type TEXT NOT NULL, resume_text TEXT NOT NULL,
  style_sample TEXT NOT NULL, uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resume_evidence (
  id INTEGER PRIMARY KEY, resume_id INTEGER NOT NULL,
  section TEXT NOT NULL, employer TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '', statement TEXT NOT NULL,
  search_terms TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(resume_id) REFERENCES resumes(id)
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY, fact TEXT NOT NULL, source TEXT NOT NULL,
  source_job_id TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
  company TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', work_type TEXT NOT NULL DEFAULT '',
  posted_at TEXT, description TEXT NOT NULL DEFAULT '', apply_url TEXT NOT NULL,
  company_url TEXT, raw_json TEXT NOT NULL DEFAULT '{}', first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, alert_id INTEGER,
  started_at TEXT NOT NULL, completed_at TEXT, parameters_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running', found_count INTEGER NOT NULL DEFAULT 0,
  shortlisted_count INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
  id INTEGER PRIMARY KEY, run_id INTEGER, provider TEXT NOT NULL,
  operation TEXT NOT NULL, request_json TEXT NOT NULL, response_count INTEGER,
  http_status INTEGER, input_tokens INTEGER, output_tokens INTEGER,
  estimated_cost_usd REAL NOT NULL DEFAULT 0, started_at TEXT NOT NULL,
  completed_at TEXT, error TEXT,
  FOREIGN KEY(run_id) REFERENCES search_runs(id)
);
CREATE TABLE IF NOT EXISTS search_results (
  run_id INTEGER NOT NULL, job_id TEXT NOT NULL, rank INTEGER NOT NULL,
  internal_score REAL NOT NULL, why TEXT NOT NULL DEFAULT '',
  questions_json TEXT NOT NULL DEFAULT '[]',
  retirement_signal TEXT NOT NULL DEFAULT 'unknown',
  retirement_evidence TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(run_id,job_id), FOREIGN KEY(run_id) REFERENCES search_runs(id),
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS job_evidence_matches (
  run_id INTEGER NOT NULL, job_id TEXT NOT NULL, evidence_id INTEGER NOT NULL,
  relevance REAL NOT NULL, PRIMARY KEY(run_id,job_id,evidence_id),
  FOREIGN KEY(run_id) REFERENCES search_runs(id),
  FOREIGN KEY(job_id) REFERENCES jobs(id),
  FOREIGN KEY(evidence_id) REFERENCES resume_evidence(id)
);
CREATE TABLE IF NOT EXISTS job_activity (
  id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, action TEXT NOT NULL,
  status TEXT, occurred_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS job_status (
  job_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, question TEXT NOT NULL,
  requirement TEXT NOT NULL, answer TEXT, detail TEXT,
  created_at TEXT NOT NULL, answered_at TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
  role_labels TEXT NOT NULL, keywords TEXT NOT NULL,
  locations TEXT NOT NULL, work_types TEXT NOT NULL,
  time_ist TEXT NOT NULL DEFAULT '08:00', count INTEGER NOT NULL DEFAULT 5,
  days_recent INTEGER NOT NULL DEFAULT 7,
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  last_sent_date_ist TEXT
);
CREATE TABLE IF NOT EXISTS alert_jobs (
  alert_id INTEGER NOT NULL, job_id TEXT NOT NULL, sent_at TEXT NOT NULL,
  PRIMARY KEY(alert_id,job_id)
);
CREATE TABLE IF NOT EXISTS email_runs (
  id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL,
  attempted_at TEXT NOT NULL, sent_at TEXT, count INTEGER NOT NULL,
  recipient_count INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL, response_id TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS quota_checks (
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, quota_name TEXT NOT NULL,
  checked_at TEXT NOT NULL, period_key TEXT NOT NULL,
  used REAL, quota_limit REAL, remaining REAL, provider_status TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS quota_notifications (
  id INTEGER PRIMARY KEY, provider TEXT NOT NULL, quota_name TEXT NOT NULL,
  period_key TEXT NOT NULL, threshold INTEGER NOT NULL,
  attempted_at TEXT NOT NULL, sent_at TEXT, status TEXT NOT NULL,
  response_id TEXT, error TEXT,
  UNIQUE(provider,quota_name,period_key,threshold)
);
CREATE TABLE IF NOT EXISTS resume_variants (
  id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, base_resume_id INTEGER NOT NULL,
  proposed_json TEXT NOT NULL, approved_json TEXT,
  created_at TEXT NOT NULL, approved_at TEXT,
  docx_path TEXT, pdf_path TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id),
  FOREIGN KEY(base_resume_id) REFERENCES resumes(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_seen ON jobs(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_activity_job ON job_activity(job_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_api_run ON api_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_quota_provider ON quota_checks(provider,checked_at);
CREATE INDEX IF NOT EXISTS idx_resume_evidence ON resume_evidence(resume_id,section);
"""


def init_db(path: Path | None = None) -> None:
    with connect(path) as db:
        db.executescript(SCHEMA)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(search_results)")}
        if "retirement_signal" not in columns:
            db.execute("ALTER TABLE search_results ADD COLUMN retirement_signal TEXT NOT NULL DEFAULT 'unknown'")
        if "retirement_evidence" not in columns:
            db.execute("ALTER TABLE search_results ADD COLUMN retirement_evidence TEXT NOT NULL DEFAULT ''")
        email_columns = {row["name"] for row in db.execute("PRAGMA table_info(email_runs)")}
        if "recipient_count" not in email_columns:
            db.execute("ALTER TABLE email_runs ADD COLUMN recipient_count INTEGER NOT NULL DEFAULT 1")
        db.execute("INSERT OR IGNORE INTO profile(id,updated_at) VALUES(1,?)", (utcnow(),))


def one(sql: str, args: tuple = ()):
    with connect() as db:
        return db.execute(sql, args).fetchone()


def all_rows(sql: str, args: tuple = ()):
    with connect() as db:
        return db.execute(sql, args).fetchall()


def write(sql: str, args: tuple = ()) -> int:
    with connect() as db:
        cursor = db.execute(sql, args)
        return cursor.lastrowid
