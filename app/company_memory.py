"""Persistent company discovery with a strict boundary around verified links."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .db import connect, one, utcnow


def name_key(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    value = re.sub(r"\b(?:private|pvt|limited|ltd|inc|corporation)\b", "", value)
    return re.sub(r"\s+", " ", value).strip()


def safe_hint(url: str | None) -> str | None:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return (url or "")[:500]


def remember_company(name: str, source: str, source_job_key: str,
                     website_hint: str | None = None) -> None:
    """Record a company even when its listing is unsuitable or cannot be verified."""
    key = name_key(name)
    if not key or not source_job_key:
        return
    now = utcnow()
    hint = safe_hint(website_hint)
    with connect() as db:
        db.execute(
            "INSERT INTO companies(name_key,name,website_hint,first_seen_at,last_seen_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(name_key) DO UPDATE SET "
            "last_seen_at=excluded.last_seen_at,"
            "website_hint=COALESCE(companies.website_hint,excluded.website_hint)",
            (key, name[:250], hint, now, now),
        )
        company_id = db.execute("SELECT id FROM companies WHERE name_key=?", (key,)).fetchone()[0]
        db.execute(
            "INSERT INTO company_observations(company_id,source,source_job_key,observed_name,"
            "website_hint,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(source,source_job_key) DO UPDATE SET "
            "company_id=excluded.company_id,last_seen_at=excluded.last_seen_at,"
            "website_hint=COALESCE(company_observations.website_hint,excluded.website_hint)",
            (company_id, source[:100], str(source_job_key)[:500], name[:250], hint, now, now),
        )


def remember_raw(raw: dict) -> None:
    name = raw.get("employer_name") or ""
    source = raw.get("source") or "JSearch"
    key = raw.get("job_uid") or raw.get("job_id") or raw.get("job_apply_link")
    if name and key:
        remember_company(str(name), str(source), str(key), raw.get("employer_website"))


def remember_verified_vacancy(name: str, vacancy_url: str,
                              website_hint: str | None = None) -> None:
    """Only call after the employer vacancy's title, company and location pass verification."""
    parsed = urlparse(vacancy_url)
    if parsed.scheme != "https" or not parsed.hostname:
        return
    key = name_key(name)
    if not key:
        return
    remember_company(name, "Verified employer vacancy", vacancy_url, website_hint)
    host = parsed.hostname.lower().removeprefix("www.")
    provider = board = career_page = None
    pieces = [part for part in parsed.path.split("/") if part]
    if host == "jobs.lever.co" and len(pieces) >= 2:
        provider, board = "Lever", pieces[0]
        career_page = f"https://jobs.lever.co/{board}"
    elif host in {"job-boards.greenhouse.io", "boards.greenhouse.io"} and len(pieces) >= 3 and pieces[1] == "jobs":
        provider, board = "Greenhouse", pieces[0]
        career_page = f"https://job-boards.greenhouse.io/{board}"
    website = safe_hint(website_hint)
    if provider:
        # The company homepage may be an unverified aggregator hint.
        website = None
    else:
        website = f"https://{parsed.hostname}"
    with connect() as db:
        db.execute(
            "UPDATE companies SET verified_website=COALESCE(verified_website,?),"
            "career_page=COALESCE(?,career_page),verified_job_url=?,"
            "ats_provider=COALESCE(?,ats_provider),ats_board=COALESCE(?,ats_board),"
            "verified_at=? WHERE name_key=?",
            (website, career_page, vacancy_url, provider, board, utcnow(), key),
        )


def verified_company(name: str):
    return one("SELECT * FROM companies WHERE name_key=? AND verified_at IS NOT NULL", (name_key(name),))


def verified_boards() -> list[tuple[str, str, str, str]]:
    with connect() as db:
        rows = db.execute(
            "SELECT ats_provider,ats_board,name,COALESCE(verified_website,website_hint,'') AS website "
            "FROM companies WHERE verified_at IS NOT NULL AND ats_provider IN ('Lever','Greenhouse') "
            "AND ats_board IS NOT NULL ORDER BY name"
        ).fetchall()
    return [(r["ats_provider"], r["ats_board"], r["name"], r["website"]) for r in rows]


def watched_company_sites(limit: int = 2) -> list[dict]:
    """Rotate through verified employer domains without crawling every site per run."""
    with connect() as db:
        rows = db.execute(
            "SELECT id,name,verified_website,last_scanned_at FROM companies "
            "WHERE verified_at IS NOT NULL AND verified_website IS NOT NULL "
            "AND ats_provider IS NULL "
            "ORDER BY last_scanned_at IS NOT NULL,last_scanned_at,last_seen_at DESC"
        ).fetchall()
    result = []
    hosts = set()
    for row in rows:
        hostname = urlparse(row["verified_website"]).hostname
        if hostname and hostname not in hosts:
            result.append(dict(row))
            hosts.add(hostname)
        if len(result) >= limit:
            break
    return result


def mark_company_scanned(company_id: int) -> None:
    with connect() as db:
        db.execute("UPDATE companies SET last_scanned_at=? WHERE id=?", (utcnow(), company_id))
