from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .db import all_rows, connect, init_db, utcnow, write
from .search import run_search

IST = ZoneInfo("Asia/Kolkata")


def parse_recipients(value: str) -> list[str]:
    addresses = [part.strip() for part in re.split(r"[,;\n]+", value) if part.strip()]
    if not 1 <= len(addresses) <= 5 or any(
        len(address) > 254 or not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", address)
        for address in addresses
    ):
        raise ValueError("Enter 1 to 5 valid email addresses, separated by commas")
    if len({address.lower() for address in addresses}) != len(addresses):
        raise ValueError("Remove duplicate email addresses")
    return addresses


def send_email(to_address: str | list[str], subject: str, content: str) -> str:
    if not settings.resend_api_key or not settings.email_from:
        raise RuntimeError("Email delivery is not configured yet")
    with httpx.Client(timeout=30) as client:
        response = client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}",
                     "Content-Type": "application/json"},
            json={"from": settings.email_from,
                  "to": [to_address] if isinstance(to_address, str) else to_address,
                  "subject": subject, "html": content},
        )
    response.raise_for_status()
    return response.json().get("id", "")


def run_alert(alert) -> int:
    alert_id = alert["id"]
    recipients = parse_recipients(alert["email"])
    run_id = run_search(
        kind="alert", alert_id=alert_id, roles=alert["role_labels"],
        keywords=alert["keywords"], locations=alert["locations"],
        work_types=alert["work_types"], count=max(20, min(100, alert["count"] * 5)),
        days_recent=alert["days_recent"],
    )
    jobs = all_rows(
        "SELECT j.*,r.why FROM search_results r JOIN jobs j ON j.id=r.job_id "
        "WHERE r.run_id=? AND NOT EXISTS(SELECT 1 FROM alert_jobs a "
        "WHERE a.alert_id=? AND a.job_id=j.id) ORDER BY r.rank LIMIT ?",
        (run_id, alert_id, alert["count"]),
    )
    email_id = write(
        "INSERT INTO email_runs(alert_id,attempted_at,count,recipient_count,status) VALUES(?,?,?,?,?)",
        (alert_id, utcnow(), len(jobs), len(recipients), "sending"),
    )
    body = ["<div style='font-family:Arial,sans-serif;max-width:620px'>",
            f"<h2>{html.escape(alert['name'])}</h2>"]
    if not jobs:
        body.append("<p>No new suitable listings were found today. The alert will check again tomorrow.</p>")
    for job in jobs:
        link = f"{settings.app_base_url}/jobs/{job['id']}"
        body.append(
            f"<p><a href='{html.escape(link, quote=True)}'><strong>{html.escape(job['title'])}</strong></a>"
            f"<br>{html.escape(job['company'])} · {html.escape(job['location'])}"
            f"<br>{html.escape(job['why'])}</p>"
        )
    body.append("<p>Open the job in your private app to review details and apply.</p></div>")
    try:
        response_id = send_email(recipients,
                                 f"{len(jobs)} new jobs — {alert['name']}", "".join(body))
        today = datetime.now(IST).date().isoformat()
        with connect() as db:
            db.execute(
                "UPDATE email_runs SET sent_at=?,status='sent',response_id=? WHERE id=?",
                (utcnow(), response_id, email_id),
            )
            db.execute("UPDATE alerts SET last_sent_date_ist=? WHERE id=?", (today, alert_id))
            for job in jobs:
                db.execute(
                    "INSERT OR IGNORE INTO alert_jobs(alert_id,job_id,sent_at) VALUES(?,?,?)",
                    (alert_id, job["id"], utcnow()),
                )
        return len(jobs)
    except Exception as exc:
        write("UPDATE email_runs SET status='failed',error=? WHERE id=?",
              (str(exc)[:500], email_id))
        raise


def run_due_alerts(now: datetime | None = None) -> list[tuple[int, str]]:
    if not all((settings.jsearch_api_key, settings.openrouter_api_key,
                settings.resend_api_key, settings.email_from)):
        return []
    now = (now or datetime.now(IST)).astimezone(IST)
    today, time_now = now.date().isoformat(), now.strftime("%H:%M")
    alerts = all_rows(
        "SELECT * FROM alerts WHERE enabled=1 AND time_ist<=? "
        "AND (last_sent_date_ist IS NULL OR last_sent_date_ist<>?)",
        (time_now, today),
    )
    outcomes = []
    for alert in alerts:
        try:
            count = run_alert(alert)
            outcomes.append((alert["id"], f"sent {count}"))
        except Exception as exc:
            outcomes.append((alert["id"], f"failed: {exc}"))
    return outcomes


if __name__ == "__main__":
    init_db()
    print(json.dumps(run_due_alerts()))
    sys.exit(0)
