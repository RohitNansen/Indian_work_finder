from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from .ai import rank_jobs, month_spend
from .config import settings
from .db import all_rows, connect, one, utcnow, write
from .evidence import current_evidence, evidence_matches
from .matching import normalize, words, role_variants, location_matches, recent

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
    queue=deque();seen=set()
    # Cover every role before repeating a role with extra skill constraints.
    for round_no in range(max(3,len(locations))):
        for index,role in enumerate(roles):
            variants=role_variants(role)
            term=variants[round_no%len(variants)]
            place=locations[(index+round_no)%len(locations)]
            remote='remote' in place.lower()
            query=f"{term} jobs in {'India remote' if remote else place}"
            if query not in seen:queue.append((query,remote,None));seen.add(query)
    # Skills expand the possible evidence; they do not all become mandatory query terms.
    for index,skill in enumerate((keywords or [])[:6]):
        query=f"{normalize(skill)} engineering lead jobs in {locations[index%len(locations)]}"
        if query not in seen:queue.append((query,False,None));seen.add(query)
    return queue


def fetch_page(run_id: int, query: str, remote: bool, cursor: str | None,
               days_recent: int) -> tuple[list[dict], str | None]:
    if month_spend() >= settings.monthly_spend_limit_usd:
        raise QuotaExceeded('Monthly app spending limit reached')
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
        "id": jid, "source": raw.get("source","JSearch"), "title": raw.get("job_title") or "",
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
    title = normalize(job["title"])
    description = normalize(job["description"])
    role_terms = [normalize(x) for x in roles]
    key_terms = [normalize(x) for x in keywords]
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
               work_types: str, count: int, days_recent: int = 30,
               alert_id: int | None = None, existing_run_id: int | None = None) -> int:
    import time
    from .direct_links import resolve_job, find_employer_on_web, discover_employer_vacancies
    if not settings.jsearch_api_key:raise RuntimeError("JSearch is not configured yet")
    roles_list=labels(roles) or DEFAULT_ROLES
    location_list=labels(locations) or ['Chennai','Tamil Nadu','Bengaluru','Remote India']
    keywords_list=labels(keywords);count=max(1,min(int(count),100))
    params={'roles':roles_list,'keywords':keywords_list,'locations':location_list,'work_types':labels(work_types),'count':count,'days_recent':days_recent}
    run_id=existing_run_id or write('INSERT INTO search_runs(kind,alert_id,started_at,parameters_json) VALUES(?,?,?,?)',(kind,alert_id,utcnow(),json.dumps(params)))
    write('UPDATE search_runs SET parameters_json=? WHERE id=?',(json.dumps(params),run_id))
    state={'stage':'Starting search','queries':0,'found':0,'checked':0,'matched':0,'current':'','stop_reason':''}
    def progress(stage, current=''):
        state.update(stage=stage,current=current)
        write('UPDATE search_runs SET progress_json=?,found_count=?,shortlisted_count=? WHERE id=?',(json.dumps(state),state['found'],state['matched'],run_id))
    previous_ids={row['job_id'] for row in all_rows('SELECT DISTINCT job_id FROM search_results WHERE run_id<?',(run_id,))}
    started=time.monotonic();seen={};processed=set();selected=[];calls=0;web_calls=0;quota_limited=False;failures=0
    context=profile_context();resume_evidence=current_evidence()
    def new_matches():
        return sum(item['job']['id'] not in previous_ids for item in selected)
    def web_lookup(job):
        nonlocal web_calls
        if web_calls>=settings.employer_web_lookups_per_run:return []
        web_calls+=1;state['queries']+=1
        return find_employer_on_web(job,run_id)
    def ingest(rows):
        added=0
        for raw in rows:
            if not acceptable_job(raw) or not work_type_matches(raw,params['work_types']):continue
            # Exact freshness/location are checked again on the employer vacancy.
            if not location_matches(raw.get('job_location',''),location_list,bool(raw.get('job_is_remote'))):continue
            stamp=raw.get('job_posted_at_datetime_utc') or raw.get('job_posted_at')
            if stamp and not recent(stamp,days_recent):continue
            job=save_job(raw)
            if job['id'] in seen:continue
            seen[job['id']]=job;added+=1
        state['found']=len(seen)
        return added
    def assess(limit=8):
        available=[j for jid,j in seen.items() if jid not in processed]
        available=[j for j in available if not any(term in normalize(j['title']) for term in ('junior','intern','trainee','entry level'))]
        available.sort(key=lambda j:deterministic_score(j,roles_list,keywords_list,context),reverse=True)
        verified=[]
        for job in available[:limit]:
            if state['checked']>=settings.openrouter_max_jobs_per_run or time.monotonic()-started>540:break
            processed.add(job['id']);state['checked']+=1
            progress('Checking company vacancies',job['title']+' · '+job['company'])
            resolved=resolve_job(job,force=job['id'] in previous_ids,run_id=run_id,web_discover=web_lookup)
            if resolved.get('direct_status')!='verified':continue
            evidence=json.loads(resolved.get('verified_json') or '{}')
            if not location_matches(resolved['location'],location_list,evidence.get('remote',False)):continue
            if not recent(resolved.get('posted_at'),days_recent):continue
            if any(x['job']['direct_url']==resolved['direct_url'] for x in selected):continue
            verified.append(resolved)
        if not verified:return
        progress('Matching verified job descriptions')
        ai={}
        if settings.openrouter_api_key:
            try:ai=rank_jobs(run_id,context,verified)
            except Exception:pass
        for job in verified:
            evaluation=ai.get(job['id'],{})
            base=deterministic_score(job,roles_list,keywords_list,context)
            # An unavailable model is not permission to claim a strong profile match.
            if settings.openrouter_api_key and not evaluation:continue
            if evaluation and evaluation.get('relevance',0)<55:continue
            if not evaluation and base<35:continue
            score=float(evaluation.get('relevance',base))
            selected.append({'job':job,'score':score,'evaluation':evaluation})
        selected.sort(key=lambda x:(x['job']['id'] not in previous_ids,x['score'],x['job'].get('posted_at') or ''),reverse=True)
        state['matched']=min(count,len(selected));progress('Continuing search')
    try:
        queue=query_queue(roles_list,location_list,keywords_list)
        no_new=0
        # A small direct-career search complements the aggregator with different sources.
        groups=[roles_list[i:i+2] for i in range(0,len(roles_list),2)]
        for group in groups:
            if not group or web_calls>=max(0,settings.employer_web_lookups_per_run-2):break
            web_calls+=1;state['queries']+=1;progress('Searching company career pages',', '.join(group))
            ingest(discover_employer_vacancies(group,location_list,days_recent,run_id,keywords_list))
            if new_matches()>=count:break
        assess(12)
        while queue and calls<settings.jsearch_max_requests_per_run and new_matches()<count:
            if time.monotonic()-started>540:state['stop_reason']='Search time limit reached';break
            if state['checked']>=settings.openrouter_max_jobs_per_run:state['stop_reason']='Vacancy checking budget reached';break
            query,remote,cursor=queue.popleft();calls+=1;state['queries']+=1;progress('Searching related roles',query)
            try:rows,next_cursor=fetch_page(run_id,query,remote,cursor,days_recent)
            except QuotaExceeded as exc:quota_limited=True;state['stop_reason']=str(exc);break
            except Exception:failures+=1;continue
            added=ingest(rows);no_new=0 if added else no_new+1
            progress('Reviewing newly found roles',query)
            if calls%3==0:assess(8)
            if next_cursor and added and calls>=len(roles_list):queue.append((query,remote,next_cursor))
            if no_new>=4 and calls>=len(roles_list):state['stop_reason']='Several searches found no new suitable vacancies';break
        # If today's sources omit a previously shown vacancy, check the employer
        # page again before offering it as a still-open result.
        if not selected and previous_ids and state['checked']<settings.openrouter_max_jobs_per_run:
            prior=all_rows("SELECT j.* FROM search_results r JOIN jobs j ON j.id=r.job_id WHERE r.run_id<? AND j.direct_status='verified' AND j.verification_version=3 GROUP BY j.id ORDER BY MAX(r.run_id) DESC LIMIT 20",(run_id,))
            for row in prior:
                job=dict(row)
                if job['id'] not in seen and location_matches(job['location'],location_list,json.loads(job.get('verified_json') or '{}').get('remote',False)) and recent(job.get('posted_at'),days_recent):
                    seen[job['id']]=job
            state['found']=len(seen)
        if quota_limited and not seen:raise RuntimeError(state['stop_reason']+' before any listings could be retrieved')
        if len(selected)<count:assess(settings.openrouter_max_jobs_per_run-state['checked'])
        if not state['stop_reason']:
            state['stop_reason']='Requested number of new jobs found' if new_matches()>=count else 'Search budget completed' if queue else 'Available searches completed'
        with connect() as db:
            for rank,item in enumerate(selected[:count],1):
                job=item['job'];e=item['evaluation'];signal=e.get('retirement_signal','unknown');quote=e.get('retirement_evidence','')
                if not quote or quote.lower() not in job['description'].lower():signal='unknown';quote=''
                db.execute('INSERT INTO search_results(run_id,job_id,rank,internal_score,why,questions_json,retirement_signal,retirement_evidence,seen_before) VALUES(?,?,?,?,?,?,?,?,?)',(run_id,job['id'],rank,item['score'],e.get('why','Relevant experience and responsibilities'),json.dumps(e.get('unconfirmed',[])[:9]),signal,quote,int(job['id'] in previous_ids)))
                db.executemany('INSERT INTO job_evidence_matches(run_id,job_id,evidence_id,relevance) VALUES(?,?,?,?)',[(run_id,job['id'],row['id'],strength) for strength,row in evidence_matches(job,resume_evidence)])
            status='partial_quota' if quota_limited else 'complete'
            if failures and not seen:status='failed'
            db.execute('UPDATE search_runs SET completed_at=?,status=?,found_count=?,shortlisted_count=?,error=? WHERE id=?',(utcnow(),status,len(seen),min(count,len(selected)),'Provider requests failed' if status=='failed' else None,run_id))
        progress('Search finished')
        return run_id
    except Exception as exc:
        state['stop_reason']='Search interrupted';progress('Could not finish')
        write("UPDATE search_runs SET completed_at=?,status='failed',error=? WHERE id=?",(utcnow(),str(exc)[:500],run_id));raise
