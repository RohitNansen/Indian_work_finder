"""Small, free employer-owned job feeds for this single-client search."""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .db import utcnow, write
from .company_memory import verified_boards

# Curated employer boards can be expanded after measuring actual suitable yield.
BOARDS = (
    ('Lever', 'dozee', 'Dozee', 'https://www.dozee.health'),
    ('Greenhouse', 'instawork', 'Instawork', 'https://www.instawork.com'),
)
TITLE_TOPICS = re.compile(r'\b(hardware|quality|robotic\w*|automation|medical|electrical|electronics|research|r&d|innovation|machinery|mechatronic\w*|product development)\b', re.I)
UNRELATED_TITLES = re.compile(r'\b(intern|partnerships?|procurement|sales|recruiter|marketing)\b', re.I)


def _description(value: str) -> str:
    return BeautifulSoup(html.unescape(value or ''), 'html.parser').get_text('\n', strip=True)


def _raw_job(provider, board, company, website, row):
    if provider == 'Lever':
        stamp = row.get('createdAt')
        posted = datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat() if isinstance(stamp,(int,float)) else None
        title = row.get('text') or ''
        location = (row.get('categories') or {}).get('location') or ''
        description = row.get('descriptionPlain') or _description(row.get('description') or '')
        url = row.get('hostedUrl') or ''
        employment = (row.get('categories') or {}).get('commitment') or ''
        identifier = row.get('id')
    else:
        posted = row.get('first_published')
        title = row.get('title') or ''
        location = (row.get('location') or {}).get('name') or ''
        description = _description(row.get('content') or '')
        url = row.get('absolute_url') or ''
        employment = ''
        identifier = row.get('id')
    if not identifier or not url.startswith('https://') or not TITLE_TOPICS.search(title) or UNRELATED_TITLES.search(title):
        return None
    return {'source':provider+' '+company,'job_uid':f'{provider}:{board}:{identifier}',
            'job_title':title,'employer_name':company,'employer_website':website,
            'job_location':location,'job_country':'IN','job_employment_type':employment,
            'job_description':description,'job_apply_link':url,
            'job_posted_at_datetime_utc':posted,'job_is_active':True,
            'job_is_remote':'remote' in location.lower()}


def discover_ats_vacancies(run_id: int | None = None) -> list[dict]:
    """Use public, company-scoped feeds. No API key and no unbounded crawl."""
    found=[]
    boards=list(BOARDS)
    known={(provider,board) for provider,board,_,_ in boards}
    for entry in verified_boards():
        if entry[:2] not in known:
            boards.append(entry)
            known.add(entry[:2])
    with httpx.Client(timeout=15, trust_env=False) as client:
        for provider, board, company, website in boards[:40]:
            url=(f'https://api.lever.co/v0/postings/{board}?mode=json' if provider=='Lever'
                 else f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true')
            call_id=write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) VALUES(?,?,?,?,?)',
                          (run_id,provider,'public_company_board',json.dumps({'company':company,'url':url}),utcnow()))
            try:
                response=client.get(url)
                response.raise_for_status()
                payload=response.json()
                jobs=payload if provider=='Lever' else payload.get('jobs',[])
                rows=[job for row in jobs if (job:=_raw_job(provider,board,company,website,row))]
                found.extend(rows)
                write('UPDATE api_calls SET response_count=?,http_status=?,completed_at=? WHERE id=?',
                      (len(rows),response.status_code,utcnow(),call_id))
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
                status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
                write('UPDATE api_calls SET http_status=?,error=?,completed_at=? WHERE id=?',
                      (status,str(exc)[:300],utcnow(),call_id))
    return found
