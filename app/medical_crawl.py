"""Bounded, free scan of official medical career pages before paid sources."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .company_memory import mark_company_scanned
from .db import write, utcnow
from .direct_links import ATS, cited_employer_vacancy, read_page, under

ROLE_WORDS = re.compile(r'\b(?:research|r&d|quality|engineering|hardware|electrical|electronics|'
                        r'automation|robotics|product|design|innovation|technical|regulatory|'
                        r'validation|medical|manufacturing|director|head|consultant)\b', re.I)
JOB_PATH = re.compile(r'/(?:job|jobs|position|positions|vacanc(?:y|ies))/', re.I)


def _links(page_url: str, html: str) -> list[str]:
    """Follow employer links or employer-linked ATS jobs; never aggregators."""
    host = (urlparse(page_url).hostname or '').lower()
    found = []
    for a in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        url = urljoin(page_url, a['href']).split('#', 1)[0]
        parsed = urlparse(url)
        target_host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or
                (target_host != host and not under(target_host, ATS)) or
                url == page_url or not JOB_PATH.search(parsed.path + '/') or
                not ROLE_WORDS.search(a.get_text(' ', strip=True))):
            continue
        # An index path such as /jobs/ is not an individual vacancy.
        tail = parsed.path.rstrip('/').split('/')[-1]
        if tail.lower() in {'job', 'jobs', 'position', 'positions', 'vacancy', 'vacancies'}:
            continue
        if url not in found:
            found.append(url)
    return found


def crawl_medical_companies(companies: list[dict], days: int, run_id: int,
                            *, max_job_pages: int = 18, progress=None) -> list[dict]:
    """Rotate a small slice each run; every emitted job has verified JSON-LD."""
    jobs = []
    pages = 0
    for index, company in enumerate(companies, 1):
        if progress:
            progress(f"{company['name']} ({index}/{len(companies)})")
        url = company['website_hint']
        call_id = write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) '
                        'VALUES(?,?,?,?,?)',
                        (run_id, 'Employer website', 'scan_medical_career_page',
                         json.dumps({'company': company['name'], 'url': url}), utcnow()))
        try:
            final, html = read_page(url)
            links = _links(final, html)
            write('UPDATE api_calls SET response_count=?,http_status=200,completed_at=? WHERE id=?',
                  (len(links), utcnow(), call_id))
            for vacancy_url in links[:4]:
                if pages >= max_job_pages:
                    break
                pages += 1
                job = cited_employer_vacancy(vacancy_url, company['name'], final, days, run_id)
                if job:
                    jobs.append(job)
        except Exception as exc:
            write('UPDATE api_calls SET response_count=0,error=?,completed_at=? WHERE id=?',
                  (str(exc)[:250], utcnow(), call_id))
        finally:
            mark_company_scanned(company['id'])
    return jobs
