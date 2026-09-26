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
from .evidence import current_evidence, evidence_matches

DEFAULT_ROLES = [
    "R&D Director", "Research and Development Consultant", "Head of Quality",
    "Technical Advisor", "Product Development Lead", "Innovation Consultant",
]


class QuotaExceeded(RuntimeError):
    pass


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
    if raw.get("job_is_active") is False:
        return False
    expires = raw.get("job_offer_expiration_datetime_utc")
    if expires:
        try:
            expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry < datetime.now(timezone.utc):
                return False
        except ValueError:
            pass
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


def query_queue(roles: list[str], locations: list[str], keywords: list[str] | None = None):
    queue = deque()
    keywords = keywords or []
    # Rotate locations so the early request budget covers every role and place.
    for offset in range(len(locations)):
        for index, role in enumerate(roles):
            location = locations[(index + offset) % len(locations)]
            remote = "remote" in location.lower()
            query = f"{role} {'remote in India' if remote else 'jobs in ' + location}"
            queue.append((query, remote, None))
            if keywords:
                role_words = set(re.findall(r"[a-z]{4,}", role.lower()))
                matching = [k for k in keywords if role_words & set(re.findall(r"[a-z]{4,}", k.lower()))]
                term = (matching or keywords)[(index + offset) % len(matching or keywords)]
                queue.append((f"{role} {term} {'remote in India' if remote else 'jobs in ' + location}", remote, None))
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
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        write("UPDATE api_calls SET http_status=?,error=?,completed_at=? WHERE id=?",
              (status, str(exc)[:500], utcnow(), call_id))
        if status in (402, 429):
            raise QuotaExceeded(f"JSearch returned HTTP {status}") from exc
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
    logo = raw.get("employer_logo") or ""
    logo = logo if logo.startswith("https://") else ""
    write("UPDATE jobs SET logo_url=? WHERE id=?", (logo,jid))
    return dict(one("SELECT * FROM jobs WHERE id=?", (jid,)))


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
        "Answered experience questions (do not infer skills from No, Not sure or Removed):",
        *[f"{x['requirement']}: {x['answer']}" for x in all_rows("SELECT requirement,answer FROM questions WHERE answered_at IS NOT NULL")],
        "Search preferences:", profile["role_labels"], profile["keywords"], profile["notes"],
    ])


def run_search(*, kind: str, roles: str, keywords: str, locations: str,
               work_types: str, count: int, days_recent: int = 7,
               alert_id: int | None = None, existing_run_id: int | None = None) -> int:
    if not settings.jsearch_api_key:
        raise RuntimeError("JSearch is not configured yet")
    roles_list = labels(roles) or DEFAULT_ROLES
    location_list = labels(locations) or ["Chennai", "Tamil Nadu", "Bengaluru", "Remote India"]
    keywords_list = labels(keywords)
    count = max(1, min(int(count), 100))
    params = {"roles": roles_list, "keywords": keywords_list, "locations": location_list,
              "work_types": labels(work_types), "count": count, "days_recent": days_recent}
    run_id = existing_run_id or write(
        "INSERT INTO search_runs(kind,alert_id,started_at,parameters_json) VALUES(?,?,?,?)",
        (kind, alert_id, utcnow(), json.dumps(params, ensure_ascii=False)),
    )
    write("UPDATE search_runs SET parameters_json=? WHERE id=?", (json.dumps(params,ensure_ascii=False),run_id))
    try:
        queue = query_queue(roles_list, location_list, keywords_list)
        seen: dict[str, dict] = {}
        seen_signatures: set[str] = set()
        calls = 0
        successful_calls = 0
        quota_limited = False
        # Search enough alternatives to curate the requested number, within a clear API cap.
        minimum_coverage = min(len(roles_list), settings.jsearch_max_requests_per_run)
        while queue and calls < max(1, settings.jsearch_max_requests_per_run // 2) and (len(seen) < count * 4 or calls < minimum_coverage):
            query, remote, cursor = queue.popleft()
            try:
                page, next_cursor = fetch_page(run_id, query, remote, cursor, days_recent)
            except QuotaExceeded:
                quota_limited = True
                break
            except Exception:
                calls += 1
                continue
            calls += 1
            successful_calls += 1
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
        if not successful_calls:
            raise RuntimeError("JSearch quota reached" if quota_limited else "JSearch could not return jobs")
        context = profile_context() + f"\nCurrent search work types: {work_types}\nLocations: {locations}"
        resume_evidence = current_evidence()
        matches_by_job = {j["id"]: evidence_matches(j, resume_evidence) for j in seen.values()}
        scored = [(deterministic_score(j, roles_list, keywords_list, context)
                   + min(20, sum(score for score, _ in matches_by_job[j["id"]]) * 0.35), j)
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
            signal = evaluation.get("retirement_signal", "unknown")
            evidence = evaluation.get("retirement_evidence", "")
            if not evidence or evidence.lower() not in job["description"].lower():
                signal, evidence = "unknown", ""
            if evaluation:
                score = 0.8 * max(0, min(100, float(evaluation.get("relevance", 0)))) + 0.2 * min(100, base)
            else:
                score = min(100, base) * (0.5 if ai else 1.0)
            if signal == "welcomes_retired":
                score += 8
            elif signal == "explicit_restriction":
                score -= 20
            why = evaluation.get("why") or "Relevant senior experience and search preferences"
            questions = evaluation.get("unconfirmed") or []
            # A requested result count is a ceiling, never a reason to fill with weak roles.
            if evaluation and float(evaluation.get("relevance",0)) < 55:
                continue
            if not evaluation and base < 30:
                continue
            ordered.append((score, job, why, questions, signal, evidence))
        ordered.sort(key=lambda x: x[0], reverse=True)
        from .direct_links import resolve_job, find_employer_on_web
        def discover(job):
            nonlocal calls
            if calls >= settings.jsearch_max_requests_per_run:
                return []
            calls += 1
            try:
                rows, _ = fetch_page(run_id, f'{job["title"]} {job["company"]} careers India', False, None, days_recent)
                return rows
            except Exception:
                return []
        web_calls=0
        def web_discover(job):
            nonlocal web_calls
            if web_calls >= settings.employer_web_lookups_per_run:
                return []
            web_calls+=1
            return find_employer_on_web(job,run_id)
        verified = []
        for item in ordered[:settings.openrouter_max_jobs_per_run]:
            resolved = resolve_job(item[1], discover=discover, run_id=run_id, web_discover=web_discover)
            if resolved.get("direct_status") == "verified":
                verified.append(item)
            if len(verified) >= count:
                break
        with connect() as db:
            for rank, (score, job, why, questions, signal, evidence) in enumerate(verified, 1):
                db.execute(
                    "INSERT INTO search_results(run_id,job_id,rank,internal_score,why,questions_json,"
                    "retirement_signal,retirement_evidence) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, job["id"], rank, score, why,
                     json.dumps(questions[:9], ensure_ascii=False), signal, evidence),
                )
                db.executemany(
                    "INSERT INTO job_evidence_matches(run_id,job_id,evidence_id,relevance) "
                    "VALUES(?,?,?,?)",
                    [(run_id, job["id"], row["id"], strength)
                     for strength, row in matches_by_job[job["id"]]],
                )
            db.execute(
                "UPDATE search_runs SET completed_at=?,status=?,found_count=?,"
                "shortlisted_count=?,error=? WHERE id=?",
                (utcnow(), "partial_quota" if quota_limited else "complete", len(seen),
                 len(verified), "JSearch quota reached during search" if quota_limited else None,
                 run_id),
            )
        return run_id
    except Exception as exc:
        write("UPDATE search_runs SET completed_at=?,status='failed',error=? WHERE id=?",
              (utcnow(), str(exc)[:500], run_id))
        raise
