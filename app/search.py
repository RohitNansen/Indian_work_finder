from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from .ai import rank_jobs
from .config import settings
from .db import all_rows, connect, one, utcnow, write

DEFAULT_ROLES = [
    "R&D Director", "Research and Development Consultant", "Head of Quality",
    "Technical Advisor", "Product Development Lead", "Innovation Consultant",
]


def labels(value: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in re.split(r"[,\n]", value or "") if x.strip()))


def normalized_link(value: str) -> str:
    return (value or "").split("?", 1)[0].rstrip("/").lower()


def job_key(raw: dict) -> str:
    uid = raw.get("job_uid") or raw.get("job_id")
    if uid:
        return hashlib.sha256(str(uid).encode()).hexdigest()[:24]
    basis = "|".join([raw.get("job_title", ""), raw.get("employer_name", ""),
                      raw.get("job_location", ""), normalized_link(raw.get("job_apply_link", ""))])
    return hashlib.sha256(basis.lower().encode()).hexdigest()[:24]


def choose_apply_url(raw: dict) -> str:
    options = raw.get("apply_options") or []
    direct = next((x.get("apply_link") for x in options if x.get("is_direct")
                   and x.get("apply_link", "").startswith("https://")), None)
    return direct or raw.get("job_apply_link") or ""


def acceptable_job(raw: dict) -> bool:
    url = choose_apply_url(raw)
    if not url.startswith("https://") or not urlparse(url).netloc:
        return False
    if not raw.get("job_title") or not raw.get("employer_name"):
        return False
    country = (raw.get("job_country") or "").upper()
    if country and country != "IN":
        description = (raw.get("job_description") or "").lower()
        if not (raw.get("job_is_remote") and ("india" in description or "worldwide" in description)):
            return False
    return True


def work_type_matches(raw: dict, requested: list[str]) -> bool:
    if not requested or len(requested) >= 4:
        return True
    aliases = {
        "consulting": ("consult", "contract", "advisor"),
        "contract": ("contract", "consult", "freelance"),
        "part-time": ("part", "fractional"),
        "full-time": ("full", "permanent"),
    }
    text = " ".join([str(raw.get("job_employment_type") or ""),
                     str(raw.get("job_title") or "")]).lower()
    if not text.strip():
        return True
    return any(any(alias in text for alias in aliases.get(kind.lower(), (kind.lower(),)))
               for kind in requested)


def date_filter(days_recent: int) -> str:
    if days_recent <= 1:
        return "today"
    if days_recent <= 3:
        return "3days"
    if days_recent <= 7:
        return "week"
    if days_recent <= 30:
        return "month"
    return "all"


def query_queue(roles: list[str], locations: list[str]):
    queue = deque()
    for role in roles:
        for location in locations:
            remote = "remote" in location.lower()
            query = f"{role} {'remote in India' if remote else 'jobs in ' + location}"
            queue.append((query, remote, None))
    return queue


def fetch_page(run_id: int, query: str, remote: bool, cursor: str | None,
               days_recent: int) -> tuple[list[dict], str | None]:
    params = {"query": query, "country": "in", "language": "en",
              "date_posted": date_filter(days_recent)}
    if remote:
        params["work_from_home"] = "true"
    if cursor:
        params["cursor"] = cursor
    call_id = write(
        "INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) VALUES(?,?,?,?,?)",
        (run_id, "JSearch", "search-v2", json.dumps(params), utcnow()),
    )
    try:
        with httpx.Client(timeout=45) as client:
            response = client.get(
                "https://api.openwebninja.com/jsearch/search-v2",
                params=params, headers={"x-api-key": settings.jsearch_api_key},
            )
        response.raise_for_status()
        body = response.json()
        payload = body.get("data") or {}
        if isinstance(payload, list):
            jobs, next_cursor = payload, body.get("cursor")
        else:
            jobs = payload.get("jobs") or payload.get("results") or []
            next_cursor = payload.get("cursor") or payload.get("next_cursor")
        write(
            "UPDATE api_calls SET response_count=?,http_status=?,estimated_cost_usd=?,"
            "completed_at=? WHERE id=?",
            (len(jobs), response.status_code, 0.005, utcnow(), call_id),
        )
        return jobs, next_cursor
    except Exception as exc:
        write("UPDATE api_calls SET error=?,completed_at=? WHERE id=?",
              (str(exc)[:500], utcnow(), call_id))
        raise


def save_job(raw: dict) -> dict:
    jid = job_key(raw)
    job = {
        "id": jid, "source": "JSearch", "title": raw.get("job_title") or "",
        "company": raw.get("employer_name") or "",
        "location": raw.get("job_location") or "",
        "work_type": raw.get("job_employment_type") or "",
        "posted_at": raw.get("job_posted_at_datetime_utc") or raw.get("job_posted_at"),
        "description": raw.get("job_description") or "",
        "apply_url": choose_apply_url(raw),
        "company_url": raw.get("employer_website"),
    }
    now = utcnow()
    with connect() as db:
        db.execute(
            "INSERT INTO jobs(id,source,title,company,location,work_type,posted_at,description,"
            "apply_url,company_url,raw_json,first_seen_at,last_seen_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,company=excluded.company,"
            "location=excluded.location,work_type=excluded.work_type,posted_at=excluded.posted_at,"
            "description=excluded.description,apply_url=excluded.apply_url,"
            "company_url=excluded.company_url,raw_json=excluded.raw_json,last_seen_at=excluded.last_seen_at",
            (jid, job["source"], job["title"], job["company"], job["location"],
             job["work_type"], job["posted_at"], job["description"], job["apply_url"],
             job["company_url"], json.dumps(raw, ensure_ascii=False), now, now),
        )
    return job


def deterministic_score(job: dict, roles: list[str], keywords: list[str], profile: str) -> float:
    title = job["title"].lower()
    description = job["description"].lower()
    role_terms = [x.lower() for x in roles]
    key_terms = [x.lower() for x in keywords]
    score = 15.0
    score += 25 if any(x in title for x in role_terms) else 0
    score += sum(4 for x in key_terms if x in title)
    score += min(20, sum(2 for x in key_terms if x in description))
    profile_words = set(re.findall(r"[a-z]{4,}", profile.lower()))
    title_words = set(re.findall(r"[a-z]{4,}", title))
    score += min(20, 4 * len(profile_words & title_words))
    if any(x in title for x in ("intern", "graduate trainee", "junior", "entry level")):
        score -= 35
    if any(x in title for x in ("director", "head", "lead", "senior", "advisor", "consultant")):
        score += 10
    return score


def profile_context() -> str:
    profile = one("SELECT * FROM profile WHERE id=1")
    resume = one("SELECT resume_text FROM resumes ORDER BY id DESC LIMIT 1")
    facts = all_rows("SELECT fact FROM facts WHERE active=1 ORDER BY id")
    return "\n".join([
        resume["resume_text"] if resume else "",
        "Confirmed additional experience:", *[x["fact"] for x in facts],
        "Search preferences:", profile["role_labels"], profile["keywords"], profile["notes"],
    ])


def run_search(*, kind: str, roles: str, keywords: str, locations: str,
               work_types: str, count: int, days_recent: int = 7,
               alert_id: int | None = None) -> int:
    if not settings.jsearch_api_key:
        raise RuntimeError("JSearch is not configured yet")
    roles_list = labels(roles) or DEFAULT_ROLES
    location_list = labels(locations) or ["Chennai", "Tamil Nadu", "Bengaluru", "Remote India"]
    keywords_list = labels(keywords)
    count = max(1, min(int(count), 100))
    params = {"roles": roles_list, "keywords": keywords_list, "locations": location_list,
              "work_types": labels(work_types), "count": count, "days_recent": days_recent}
    run_id = write(
        "INSERT INTO search_runs(kind,alert_id,started_at,parameters_json) VALUES(?,?,?,?)",
        (kind, alert_id, utcnow(), json.dumps(params, ensure_ascii=False)),
    )
    try:
        queue = query_queue(roles_list, location_list)
        seen: dict[str, dict] = {}
        seen_signatures: set[str] = set()
        calls = 0
        # Search enough alternatives to curate the requested number, within a clear API cap.
        while queue and calls < settings.jsearch_max_requests_per_run and len(seen) < count * 4:
            query, remote, cursor = queue.popleft()
            try:
                page, next_cursor = fetch_page(run_id, query, remote, cursor, days_recent)
            except Exception:
                calls += 1
                continue
            calls += 1
            for raw in page:
                if not acceptable_job(raw) or not work_type_matches(raw, labels(work_types)):
                    continue
                signature = "|".join([
                    str(raw.get("job_title", "")).lower().strip(),
                    str(raw.get("employer_name", "")).lower().strip(),
                    str(raw.get("job_location", "")).lower().strip(),
                ])
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                job = save_job(raw)
                seen[job["id"]] = job
            if next_cursor and len(seen) < count * 4:
                queue.append((query, remote, next_cursor))
        context = profile_context() + f"\nCurrent search work types: {work_types}\nLocations: {locations}"
        scored = [(deterministic_score(j, roles_list, keywords_list, context), j)
                  for j in seen.values()]
        scored.sort(key=lambda x: x[0], reverse=True)
        candidates = [j for _, j in scored[:min(settings.openrouter_max_jobs_per_run,
                                                  max(count * 2, 15))]]
        ai = {}
        if candidates and settings.openrouter_api_key:
            try:
                ai = rank_jobs(run_id, context, candidates)
            except Exception:
                # Search remains usable; the failure is present in the API log.
                ai = {}
        ordered = []
        for base, job in scored:
            evaluation = ai.get(job["id"], {})
            score = float(evaluation.get("relevance", base))
            why = evaluation.get("why") or "Relevant senior experience and search preferences"
            questions = evaluation.get("unconfirmed") or []
            ordered.append((score, job, why, questions))
        ordered.sort(key=lambda x: x[0], reverse=True)
        with connect() as db:
            for rank, (score, job, why, questions) in enumerate(ordered[:count], 1):
                db.execute(
                    "INSERT INTO search_results(run_id,job_id,rank,internal_score,why,questions_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (run_id, job["id"], rank, score, why,
                     json.dumps(questions[:3], ensure_ascii=False)),
                )
            db.execute(
                "UPDATE search_runs SET completed_at=?,status='complete',found_count=?,"
                "shortlisted_count=? WHERE id=?",
                (utcnow(), len(seen), min(count, len(ordered)), run_id),
            )
        return run_id
    except Exception as exc:
        write("UPDATE search_runs SET completed_at=?,status='failed',error=? WHERE id=?",
              (utcnow(), str(exc)[:500], run_id))
        raise
