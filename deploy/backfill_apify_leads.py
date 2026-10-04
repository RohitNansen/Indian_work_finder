"""Recover already-paid Naukri actor datasets into the private lead inventory."""
from __future__ import annotations

import json

import httpx

from app.company_memory import remember_company, remember_source_lead
from app.config import settings
from app.db import all_rows, one


def main() -> None:
    if not settings.apify_token:
        raise RuntimeError('Apify is not configured')
    rows = all_rows("SELECT request_json FROM api_calls WHERE provider='Apify' ORDER BY id")
    recovered = 0
    runs = 0
    with httpx.Client(timeout=30,trust_env=False,
                      headers={'Authorization':'Bearer '+settings.apify_token}) as client:
        for row in rows:
            request = json.loads(row['request_json'])
            run_id = request.get('actor_run_id') or request.get('apify_run_id')
            if not run_id:
                continue
            response=client.get(f'https://api.apify.com/v2/actor-runs/{run_id}')
            response.raise_for_status()
            run=response.json()['data']
            if run['status']!='SUCCEEDED':
                continue
            response=client.get(f'https://api.apify.com/v2/datasets/{run["defaultDatasetId"]}/items',
                                params={'clean':'true'})
            response.raise_for_status()
            for item in response.json():
                if not isinstance(item,dict):
                    continue
                remember_company(str(item.get('companyName') or ''),'Apify Naukri',
                                 str(item.get('jobId') or item.get('portalUrl') or ''),
                                 item.get('companyWebsite'))
                remember_source_lead('Apify Naukri',item)
                recovered += 1
            runs += 1
    total=one('SELECT COUNT(*) AS n FROM source_leads')['n']
    print(f'Recovered {recovered} records from {runs} prior actor runs; {total} unique leads retained.')


if __name__ == '__main__':
    main()
