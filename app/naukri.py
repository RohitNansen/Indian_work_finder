"""Capped Naukri discovery through Apify; employer-page verification remains mandatory."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

import httpx

from .ai import month_spend
from .company_memory import remember_company
from .config import settings
from .db import utcnow, write

ACTOR = 'blackfalcondata~naukri-jobs-feed'
MAX_RESULTS = 25
PRICE_PER_RESULT_USD = 0.00039
START_PRICE_USD = 0.001


def query_for_role(role: str) -> str:
    title = role.lower()
    if 'quality' in title:
        return 'supplier quality manufacturing' if 'supplier' in title else 'quality head manufacturing'
    if 'medical' in title or 'life science' in title:
        return 'medical device R&D'
    if 'robot' in title:
        return 'robotics hardware lead'
    if 'automation' in title:
        return 'industrial automation PLC'
    if 'textile' in title:
        return 'textile machinery R&D'
    if 'hardware' in title:
        return 'hardware engineering lead'
    if 'electrical' in title or 'electronics' in title:
        return 'electrical R&D lead'
    if 'product development' in title:
        return 'product development engineering head'
    if 'research' in title or 'r&d' in title:
        return 'electrical R&D manager'
    if 'consultant' in title or 'advisor' in title:
        return 'engineering consultant industrial automation'
    return role[:80]


def query_choice(roles: list[str], locations: list[str], run_id: int) -> tuple[str, str]:
    roles = roles or ['R&D Director']
    cities = []
    for location in locations:
        lower = location.lower()
        if 'remote' in lower:
            continue
        city = 'Bangalore' if 'bengaluru' in lower or 'bangalore' in lower else location.strip()
        if city.lower() == 'tamil nadu' and any('chennai' in value.lower() for value in locations):
            continue
        if city and city not in cities:
            cities.append(city)
    cities = cities or ['India']
    number = max(0, run_id - 1)
    role = roles[(number // len(cities)) % len(roles)]
    city = cities[number % len(cities)]
    return query_for_role(role), city


def plausible_industrial_role(row: dict) -> bool:
    title = str(row.get('title') or '').lower()
    detail = ' '.join(str(row.get(key) or '') for key in ('description','descriptionSnippet','roleCategory','functionalArea')).lower()
    if re.search(r'\b(?:sdet|selenium|playwright|appium|software test|test automation|java developer|python developer|cloud platform|devops|sales|accountant|recruiter|hr executive)\b', title):
        return False
    if not re.search(r'\b(?:quality|r&d|research|hardware|electrical|electronics|robotics|automation|product development|engineering head|engineering consultant)\b', title):
        return False
    if not re.search(r'\b(?:head|director|manager|lead|senior|principal|chief|consultant|advisor|architect|vp|vice president)\b', title):
        return False
    return bool(re.search(r'\b(?:manufactur\w*|supplier|hardware|electrical|electronics|embedded|robotic\w*|medical device|machiner\w*|industrial|power electronics|iso 13485|product development|r&d)\b', title+' '+detail))


def to_raw(row: dict) -> dict | None:
    if not plausible_industrial_role(row):
        return None
    job_id = row.get('jobId')
    portal = row.get('portalUrl') or row.get('staticUrl')
    if not job_id or not isinstance(portal,str) or not portal.startswith('https://'):
        return None
    company_apply = row.get('companyApplyUrl') or ''
    host = (urlparse(company_apply).hostname or '').lower()
    direct_options = ([{'apply_link':company_apply,'is_direct':True}]
                      if company_apply.startswith('https://') and not any(
                          host == board or host.endswith('.'+board)
                          for board in ('naukri.com','linkedin.com','indeed.com','foundit.in'))
                      else [])
    return {
        'source':'Apify Naukri','job_uid':'naukri:'+str(job_id),
        'job_title':row.get('title') or '', 'employer_name':row.get('companyName') or '',
        'employer_website':row.get('companyWebsite'),
        'employer_logo':row.get('logoPathV3') or row.get('logoPath'),
        'job_location':row.get('location') or '', 'job_country':'IN',
        'job_employment_type':row.get('employmentType') or '',
        'job_description':row.get('description') or row.get('descriptionSnippet') or '',
        'job_apply_link':portal,'apply_options':direct_options,
        'job_posted_at_datetime_utc':row.get('createdDate'),
        'job_is_active':True,
        'job_is_remote':str(row.get('wfhType') or '').lower() in {'remote','work from home'}
                        or 'remote' in str(row.get('location') or '').lower(),
    }


def discover_naukri_vacancies(roles: list[str], locations: list[str], days: int,
                              run_id: int | None) -> list[dict]:
    if not settings.apify_token or month_spend() + START_PRICE_USD >= settings.monthly_spend_limit_usd:
        return []
    keyword, location = query_choice(roles,locations,run_id or 1)
    payload = {'keyword':keyword,'location':location,'datePosted':str(min(days,30)),
               'experience':'10+',
               'maxResults':MAX_RESULTS,'fetchDetails':True,'descriptionFormat':'text',
               'postedBy':'Company'}
    call_id = write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) '
                    'VALUES(?,?,?,?,?)', (run_id,'Apify','naukri_discovery',json.dumps(payload),utcnow()))
    actor_run_id = None
    try:
        with httpx.Client(timeout=25, trust_env=False,
                          headers={'Authorization':'Bearer '+settings.apify_token}) as client:
            response=client.post(f'https://api.apify.com/v2/actors/{ACTOR}/runs',json=payload)
            response.raise_for_status()
            actor_run=response.json()['data']
            actor_run_id=actor_run['id']
            deadline=time.monotonic()+120
            while actor_run['status'] in {'READY','RUNNING'} and time.monotonic()<deadline:
                time.sleep(5)
                poll=client.get(f'https://api.apify.com/v2/actor-runs/{actor_run_id}')
                poll.raise_for_status()
                actor_run=poll.json()['data']
            if actor_run['status']!='SUCCEEDED':
                raise RuntimeError('Naukri actor did not finish successfully')
            response=client.get(f'https://api.apify.com/v2/datasets/{actor_run["defaultDatasetId"]}/items',
                                params={'clean':'true'})
            response.raise_for_status()
            items=response.json()
            if not isinstance(items,list):
                raise ValueError('Naukri actor returned an invalid dataset')
        for item in items:
            if isinstance(item,dict):
                remember_company(str(item.get('companyName') or ''),'Apify Naukri',
                                 str(item.get('jobId') or item.get('portalUrl') or ''),
                                 item.get('companyWebsite'))
        estimate=START_PRICE_USD+PRICE_PER_RESULT_USD*len(items)
        write('UPDATE api_calls SET request_json=?,response_count=?,http_status=200,'
              'estimated_cost_usd=?,completed_at=? WHERE id=?',
              (json.dumps({**payload,'actor_run_id':actor_run_id}),len(items),estimate,utcnow(),call_id))
        return [raw for item in items if isinstance(item,dict) and (raw:=to_raw(item))]
    except Exception as exc:
        status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
        write('UPDATE api_calls SET request_json=?,http_status=?,error=?,completed_at=? WHERE id=?',
              (json.dumps({**payload,'actor_run_id':actor_run_id}),status,str(exc)[:300],utcnow(),call_id))
        return []
