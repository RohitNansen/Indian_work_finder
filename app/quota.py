"""Check provider and local quotas, and email the owner once per threshold."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import httpx

from .alerts import send_email
from .config import settings
from .db import all_rows, connect, init_db, one, utcnow, write

THRESHOLDS = (80, 95, 100)


def record_check(provider: str, name: str, period: str, used: float | None,
                 limit: float | None, remaining: float | None,
                 status: str = "", error: str = "") -> None:
    write(
        "INSERT INTO quota_checks(provider,quota_name,checked_at,period_key,used,quota_limit,"
        "remaining,provider_status,error) VALUES(?,?,?,?,?,?,?,?,?)",
        (provider, name, utcnow(), period, used, limit, remaining, status, error[:500]),
    )


def notify(provider: str, name: str, period: str, threshold: int,
           used: float | None, limit: float | None, status: str = "") -> None:
    if not settings.quota_alert_email:
        return
    with connect() as db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO quota_notifications(provider,quota_name,period_key,threshold,"
            "attempted_at,status) VALUES(?,?,?,?,?,?)",
            (provider, name, period, threshold, utcnow(), "pending"),
        )
        if cursor.rowcount == 0:
            existing = db.execute(
                "SELECT id,status FROM quota_notifications WHERE provider=? AND quota_name=? "
                "AND period_key=? AND threshold=?",
                (provider, name, period, threshold),
            ).fetchone()
            if existing["status"] == "sent":
                return
            notification_id = existing["id"]
        else:
            notification_id = cursor.lastrowid
    usage = (f"{used:g} of {limit:g}" if used is not None and limit is not None
             else "The provider reported a limit or quota error")
    message = (f"<p><strong>{html.escape(provider)} — {html.escape(name)}</strong> has reached "
               f"the {threshold}% alert level.</p><p>Usage: {html.escape(usage)}. "
               f"Status: {html.escape(status or 'check the provider dashboard')}.</p>"
               f"<p>Period: {html.escape(period)}. Review the app's private Activity page "
               "and your provider account before further searches.</p>")
    try:
        response_id = send_email(settings.quota_alert_email,
                                 f"Job Finder quota alert: {provider} {name}", message)
        write("UPDATE quota_notifications SET status='sent',sent_at=?,response_id=?,error=NULL "
              "WHERE id=?", (utcnow(), response_id, notification_id))
    except Exception as exc:
        write("UPDATE quota_notifications SET status='failed',attempted_at=?,error=? WHERE id=?",
              (utcnow(), str(exc)[:500], notification_id))


def evaluate(provider: str, name: str, period: str, used: float | None,
             limit: float | None, status: str = "") -> None:
    remaining = max(0, limit - used) if used is not None and limit is not None else None
    record_check(provider, name, period, used, limit, remaining, status)
    if limit and used is not None:
        percent = 100 * used / limit
        for threshold in THRESHOLDS:
            if percent >= threshold:
                notify(provider, name, period, threshold, used, limit, status)
    elif status in {"exceeded", "limit_reached"}:
        notify(provider, name, period, 100, used, limit, status)


def jsearch_quota() -> None:
    if not settings.jsearch_api_key:
        return
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get("https://api.openwebninja.com/usage",
                                  params={"api_id": "jsearch"},
                                  headers={"x-api-key": settings.jsearch_api_key})
        response.raise_for_status()
        data = response.json()["data"]
        period = data.get("period_start") or datetime.now(timezone.utc).strftime("%Y-%m")
        for quota in data.get("quotas", []):
            evaluate("JSearch", quota.get("name", "Requests"), period,
                     quota.get("used"), quota.get("limit"), data.get("status", ""))
    except Exception as exc:
        record_check("JSearch", "Requests", datetime.now(timezone.utc).strftime("%Y-%m"),
                     None, None, None, "check_failed", str(exc))


def openrouter_quota() -> None:
    if not settings.openrouter_api_key:
        return
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get("https://openrouter.ai/api/v1/key",
                                  headers={"Authorization": f"Bearer {settings.openrouter_api_key}"})
        response.raise_for_status()
        data = response.json()["data"]
        reset = data.get("limit_reset")
        now = datetime.now(timezone.utc)
        period = (now.strftime("%Y-%m-%d") if reset == "daily" else
                  now.strftime("%Y-%W") if reset == "weekly" else
                  now.strftime("%Y-%m") if reset == "monthly" else "key-lifetime")
        limit = data.get("limit")
        used = (float(limit) - float(data["limit_remaining"]) if limit is not None
                and data.get("limit_remaining") is not None else None)
        evaluate("OpenRouter", "API key spend", period, used, limit,
                 "active" if data.get("limit_remaining") else "limit_reached" if limit else "uncapped")
    except Exception as exc:
        record_check("OpenRouter", "API key spend", datetime.now(timezone.utc).strftime("%Y-%m"),
                     None, None, None, "check_failed", str(exc))


def local_quotas() -> None:
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    day = now.strftime("%Y-%m-%d")
    cost = one("SELECT COALESCE(SUM(estimated_cost_usd),0) AS total FROM api_calls "
               "WHERE started_at LIKE ?", (f"{month}%",))["total"]
    evaluate("Job Finder", "configured monthly API spend", month,
             float(cost), settings.monthly_spend_limit_usd)
    sent_month = one("SELECT COALESCE(SUM(recipient_count),0) AS n FROM email_runs WHERE status='sent' AND sent_at LIKE ?",
                     (f"{month}%",))["n"]
    sent_day = one("SELECT COALESCE(SUM(recipient_count),0) AS n FROM email_runs WHERE status='sent' AND sent_at LIKE ?",
                   (f"{day}%",))["n"]
    quota_month = one("SELECT COUNT(*) AS n FROM quota_notifications WHERE status='sent' "
                      "AND sent_at LIKE ?", (f"{month}%",))["n"]
    quota_day = one("SELECT COUNT(*) AS n FROM quota_notifications WHERE status='sent' "
                    "AND sent_at LIKE ?", (f"{day}%",))["n"]
    evaluate("Resend", "app emails this month", month,
             sent_month + quota_month, settings.resend_monthly_quota)
    evaluate("Resend", "app emails today", day,
             sent_day + quota_day, settings.resend_daily_quota)
    # A provider 402/429 is actionable even when its account quota is not readable.
    for row in all_rows(
        "SELECT provider,http_status FROM api_calls WHERE http_status IN (402,429) "
        "AND started_at LIKE ? GROUP BY provider,http_status", (f"{day}%",)
    ):
        notify(row["provider"], f"HTTP {row['http_status']} limit response",
               day, 100, None, None, "provider rejected requests")


def check_quotas() -> None:
    init_db()
    local_quotas()
    jsearch_quota()
    openrouter_quota()


if __name__ == "__main__":
    check_quotas()
    print(json.dumps({"checked_at": utcnow()}))
