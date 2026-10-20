"""Bounded, free scan of official medical career pages before paid sources."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .company_memory import mark_company_scanned, remember_source_lead
from .db import all_rows, write, utcnow
from .direct_links import ATS, cited_employer_vacancy, read_page, under

ROLE_WORDS = re.compile(r'\b(?:research|r&d|quality|engineering|hardware|electrical|electronics|'
                        r'automation|robotics|product|design|innovation|technical|regulatory|'
                        r'validation|medical|manufacturing|director|head|consultant|'
                        r'biomedical|instrumentation|firmware|compliance|operations|qms|qa|qc)\b', re.I)
JOB_PATH = re.compile(r'/(?:job|jobs|position|positions|vacanc(?:y|ies))/', re.I)


def _links(page_url: str, html: str) -> list[tuple[str, str]]:
    """Follow employer links or employer-linked ATS jobs; never aggregators."""
    host = (urlparse(page_url).hostname or '').lower()
    found = []
    for a in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        url = urljoin(page_url, a['href']).split('#', 1)[0]
        parsed = urlparse(url)
        target_host = (parsed.hostname or '').lower()
        title = a.get_text(' ', strip=True) or a.get('aria-label', '') or a.get('title', '')
        if not ROLE_WORDS.search(title) and a.parent:
            title = a.parent.get_text(' ', strip=True)[:240]
        if (parsed.scheme != 'https' or
                (target_host != host and not under(target_host, ATS)) or
                url == page_url or not JOB_PATH.search(parsed.path + '/') or
                not ROLE_WORDS.search(title)):
            continue
        # An index path such as /jobs/ is not an individual vacancy.
        tail = parsed.path.rstrip('/').split('/')[-1]
        if tail.lower() in {'job', 'jobs', 'position', 'positions', 'vacancy', 'vacancies'}:
            continue
        if not any(saved_url == url for saved_url, _ in found):
            found.append((url, title[:300]))
    return found


def _role_strength(title: str, roles: list[str]) -> int:
    title = re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()
    title_words = set(title.split())
    score = max((8 if re.sub(r'[^a-z0-9]+', ' ', role.lower()).strip() in title else
                 len(title_words & set(re.findall(r'[a-z0-9]+', role.lower()))))
                for role in roles) if roles else 0
    if any(word in title_words for word in ('head', 'director', 'lead', 'manager', 'consultant', 'advisor')):
        score += 2
    if any(word in title_words for word in ('intern', 'trainee', 'junior', 'sales', 'marketing')):
        score -= 10
    return score


def _scan(company: dict) -> tuple[str, list[tuple[str, str]]]:
    final, html = read_page(company['website_hint'])
    return final, _links(final, html)


def crawl_medical_companies(companies: list[dict], days: int, run_id: int,
                            *, max_job_pages: int = 18, roles: list[str] | None = None,
                            progress=None) -> list[dict]:
    """Scan the directory with bounded parallelism, then verify the best leads."""
    jobs = []
    candidates = []
    calls = {}
    for company in companies:
        call_id = write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) '
                        'VALUES(?,?,?,?,?)',
                        (run_id, 'Employer website', 'scan_medical_career_page',
                         json.dumps({'company': company['name'], 'url': company['website_hint']}), utcnow()))
        calls[company['id']] = call_id
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(companies)))) as pool:
        futures = {pool.submit(_scan, company): company for company in companies}
        for index, future in enumerate(as_completed(futures), 1):
            company = futures[future]
            try:
                final, links = future.result()
                write('UPDATE api_calls SET response_count=?,http_status=200,completed_at=? WHERE id=?',
                      (len(links), utcnow(), calls[company['id']]))
                for vacancy_url, title in links[:50]:
                    remember_source_lead('Employer career directory', {
                        'jobId': vacancy_url, 'companyName': company['name'],
                        'title': title, 'portalUrl': vacancy_url,
                        'careerUrl': final,
                    })
                    candidates.append((vacancy_url, title, company['name'], final))
            except Exception as exc:
                write('UPDATE api_calls SET response_count=0,error=?,completed_at=? WHERE id=?',
                      (str(exc)[:250], utcnow(), calls[company['id']]))
            finally:
                mark_company_scanned(company['id'])
                if progress:
                    progress(f"{company['name']} ({index}/{len(companies)} career pages)")
    checked = {row['source_job_key']: row['checked_at'] for row in all_rows(
        "SELECT source_job_key,checked_at FROM source_leads WHERE provider='Employer career directory'")}
    candidates.sort(key=lambda item: (
        checked.get(item[0]) is not None,
        -int(_role_strength(item[1], roles or []) > 0),
        -_role_strength(item[1], roles or []),
        checked.get(item[0]) or '',
    ))
    seen_urls = set()
    for vacancy_url, _title, company_name, career_url in candidates:
        if len(seen_urls) >= max_job_pages:
            break
        if vacancy_url in seen_urls:
            continue
        seen_urls.add(vacancy_url)
        job = cited_employer_vacancy(vacancy_url, company_name, career_url, days, run_id)
        write("UPDATE source_leads SET checked_at=? WHERE provider='Employer career directory' "
              "AND source_job_key=?", (utcnow(), vacancy_url))
        if job:
            jobs.append(job)
    return jobs
