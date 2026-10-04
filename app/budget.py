"""Shared monthly budget for every paid discovery and model request.

An atomic reservation prevents concurrent searches from spending the same
remaining balance. Reservations count as spend until replaced by actual usage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from .config import settings
from .db import connect, one, utcnow


class BudgetExceeded(RuntimeError):
    pass


def month_key() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m')


def monthly_limit() -> float:
    row = one('SELECT monthly_limit_usd FROM budget_settings WHERE id=1')
    return min(settings.monthly_spend_limit_usd,
               float(row['monthly_limit_usd']) if row else settings.monthly_spend_limit_usd)


def month_spend() -> float:
    row = one('SELECT COALESCE(SUM(estimated_cost_usd),0) AS total FROM api_calls '
              'WHERE substr(started_at,1,7)=?', (month_key(),))
    return float(row['total'] if row else 0)


def search_count() -> int:
    row = one("SELECT COUNT(*) AS n FROM search_runs WHERE kind IN ('manual','alert') "
              'AND substr(started_at,1,7)=?', (month_key(),))
    return int(row['n'] if row else 0)


def set_monthly_limit(amount: float) -> None:
    if not isfinite(amount) or not 1 <= amount <= settings.monthly_spend_limit_usd:
        raise ValueError('Choose a monthly stop amount within the owner limit')
    with connect() as db:
        db.execute('INSERT INTO budget_settings(id,monthly_limit_usd,updated_at) '
                   'VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET '
                   'monthly_limit_usd=excluded.monthly_limit_usd,updated_at=excluded.updated_at',
                   (amount,utcnow()))


def reserve_call(run_id: int | None, provider: str, operation: str,
                 request_json: str, maximum_cost: float) -> int:
    """Count a conservative worst-case charge before contacting a paid API."""
    if maximum_cost <= 0:
        raise ValueError('A positive request reserve is required')
    denied = False
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT monthly_limit_usd FROM budget_settings WHERE id=1').fetchone()
        limit = min(settings.monthly_spend_limit_usd,
                    float(row['monthly_limit_usd']) if row else settings.monthly_spend_limit_usd)
        used = float(db.execute(
            'SELECT COALESCE(SUM(estimated_cost_usd),0) FROM api_calls '
            'WHERE substr(started_at,1,7)=?', (month_key(),)).fetchone()[0])
        if used + maximum_cost > limit + 1e-9:
            denied = True
        else:
            call_id = db.execute(
                'INSERT INTO api_calls(run_id,provider,operation,request_json,'
                'estimated_cost_usd,started_at) VALUES(?,?,?,?,?,?)',
                (run_id,provider,operation,request_json,maximum_cost,utcnow()),
            ).lastrowid
    if denied:
        # The notice is outside the write lock. A unique monthly record makes
        # concurrent blocked calls produce just one owner email.
        from .quota import notify
        notify('Job Finder','monthly API spending stop',month_key(),100,used,limit,
               f'Paused before the next paid request. {search_count()} searches started this month')
        raise BudgetExceeded('Monthly API spending stop reached; paid searches are paused')
    return call_id
