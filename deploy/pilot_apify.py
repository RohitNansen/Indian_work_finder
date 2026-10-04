"""Capped, one-query-at-a-time Naukri source comparison on Apify free credit.

Run from the repository root with ``.venv/bin/python -m deploy.pilot_apify 1``.
The API token is read from the private .env, never from a command argument.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import db
from app.company_memory import remember_company
from app.config import settings
from app.direct_links import BOARDS, greenhouse_vacancy_details, host, read_page, under, vacancy_details

ACTOR = 'blackfalcondata~naukri-jobs-feed'
QUERIES = (
    ('hardware engineering head', 'Bangalore'),
    ('medical device R&D', 'Bangalore'),
    ('head of quality', 'Chennai'),
    ('automation consultant', 'Chennai'),
)
MAX_RESULTS = 25


def compare(items: list[dict], *, verify_limit: int = 8) -> dict:
    recent = 0
    candidate_links = 0
    verified_links = 0
    title_hits = 0
    employer_checks = []
    previous = {(re.sub(r'\W+','',r['title'].lower()),re.sub(r'\W+','',r['company'].lower()))
                for r in db.all_rows("SELECT title,company FROM jobs WHERE source='JSearch'")}
    overlap = 0
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    for row in items:
        title = str(row.get('title') or '')
        company = str(row.get('companyName') or '')
        location = str(row.get('location') or '')
        if re.search(r'hardware|medical|electrical|robotic|automation|quality|research|r&d|product development',title,re.I):
            title_hits += 1
        if (re.sub(r'\W+','',title.lower()),re.sub(r'\W+','',company.lower())) in previous:
            overlap += 1
        try:
            stamp = datetime.fromisoformat(str(row.get('createdDate') or '').replace('Z','+00:00'))
            recent += int(stamp.timestamp()>=cutoff)
        except ValueError:
            pass
        direct = row.get('companyApplyUrl') or ''
        if not isinstance(direct,str) or not direct.startswith('https://') or under(host(direct),BOARDS):
            continue
        candidate_links += 1
        if len(employer_checks)>=verify_limit:
            continue
        job={'title':title,'company':company,'location':location,
             'company_url':row.get('companyWebsite') or ''}
        try:
            final,page=read_page(direct)
            valid,note,_=vacancy_details(final,page,job)
            if not valid and host(final) in {'job-boards.greenhouse.io','boards.greenhouse.io'}:
                valid,note,_=greenhouse_vacancy_details(final,page,job)
            verified_links += int(valid)
            employer_checks.append({'title':title,'company':company,'location':location,
                                    'url':final,'verified':valid,'reason':note})
        except Exception as exc:
            employer_checks.append({'title':title,'company':company,'location':location,
                                    'url':direct,'verified':False,'reason':str(exc)[:160]})
    return {'records':len(items),'posted_last_30_days':recent,'topic_titles':title_hits,
            'already_in_jsearch_db':overlap,'company_links_supplied':candidate_links,
            'company_links_tested':len(employer_checks),'company_links_verified':verified_links,
            'employer_checks':employer_checks}


def run_query(number: int) -> Path:
    token = os.getenv('APIFY_TOKEN','')
    if not token:
        raise RuntimeError('Save APIFY_TOKEN in the private .env before starting the pilot.')
    if not 1<=number<=len(QUERIES):
        raise ValueError('Choose a query number from 1 to 4.')
    keyword,location = QUERIES[number-1]
    payload={'keyword':keyword,'location':location,'datePosted':'30',
             'maxResults':MAX_RESULTS,'fetchDetails':True,'descriptionFormat':'text'}
    call_id=db.write('INSERT INTO api_calls(provider,operation,request_json,started_at) VALUES(?,?,?,?)',
                     ('Apify','naukri_source_pilot',json.dumps({'actor':ACTOR,**payload}),db.utcnow()))
    api='https://api.apify.com/v2'
    run_id=None
    try:
        with httpx.Client(timeout=25,headers={'Authorization':'Bearer '+token},trust_env=False) as client:
            response=client.post(f'{api}/actors/{ACTOR}/runs',json=payload)
            response.raise_for_status()
            run=response.json()['data']
            run_id=run['id']
            deadline=time.monotonic()+300
            while run['status'] in {'READY','RUNNING'} and time.monotonic()<deadline:
                time.sleep(8)
                poll=client.get(f'{api}/actor-runs/{run_id}')
                poll.raise_for_status()
                run=poll.json()['data']
            if run['status']!='SUCCEEDED':
                raise RuntimeError(f"Actor {run_id} ended with status {run['status']}; see the Apify Console run log.")
            dataset_id=run['defaultDatasetId']
            fetched=client.get(f'{api}/datasets/{dataset_id}/items',params={'clean':'true'})
            fetched.raise_for_status()
            items=fetched.json()
            if not isinstance(items,list):
                raise ValueError('Apify returned a non-list dataset.')
        estimate=0.001+0.00039*len(items)
        db.write('UPDATE api_calls SET request_json=?,response_count=?,http_status=?,estimated_cost_usd=?,completed_at=? WHERE id=?',
                 (json.dumps({'actor':ACTOR,**payload,'apify_run_id':run_id}),len(items),200,estimate,db.utcnow(),call_id))
        summary=compare(items)
        for item in items:
            if isinstance(item,dict):
                remember_company(str(item.get('companyName') or ''),'Apify Naukri',
                                 str(item.get('jobId') or item.get('portalUrl') or ''),
                                 item.get('companyWebsite'))
        output=settings.data_dir/'pilots'/f'apify-naukri-query-{number}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'query':payload,'apify_run_id':run_id,
                                      'estimated_actor_cost_usd':estimate,
                                      'summary':summary,'items':items},indent=2,ensure_ascii=False))
        output.chmod(0o600)
        print(json.dumps({'file':str(output),'query':number,'summary':{k:v for k,v in summary.items() if k!='employer_checks'},
                          'estimated_actor_cost_usd':round(estimate,4)},indent=2))
        return output
    except Exception as exc:
        db.write('UPDATE api_calls SET request_json=?,error=?,completed_at=? WHERE id=?',
                 (json.dumps({'actor':ACTOR,**payload,'apify_run_id':run_id}),str(exc)[:400],db.utcnow(),call_id))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query',type=int,choices=range(1,5),help='Pilot query 1-4; each caps results at 25')
    arguments=parser.parse_args()
    run_query(arguments.query)
