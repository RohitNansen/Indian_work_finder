"""Bounded public employer career-index adapters, starting with Baxter India."""
from __future__ import annotations

import json
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .db import utcnow, write
from .direct_links import cited_employer_vacancy, read_page
from .matching import location_matches


def discover_baxter_vacancies(locations: list[str], days: int,
                              run_id: int | None = None) -> list[dict]:
    """Search four official index pages and inspect at most twelve job pages."""
    city='Bengaluru' if location_matches('Bengaluru, Karnataka, India',locations) else (
        'Chennai' if location_matches('Chennai, Tamil Nadu, India',locations) else '')
    if not city:return []
    queries=(f'quality {city}',f'R&D {city}',f'hardware {city}',city)
    results=[];seen=set()
    for query in queries:
        if len(seen)>=12:break
        url='https://jobs.baxter.com/en/search-jobs?'+urlencode({'k':query,'orgIds':'152'})
        call_id=write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) '
                      'VALUES(?,?,?,?,?)',
                      (run_id,'Employer website','baxter_career_index',json.dumps({'query':query,'url':url}),utcnow()))
        try:
            final,page=read_page(url)
            soup=BeautifulSoup(page,'html.parser')
            links=[]
            for anchor in soup.find_all('a',href=True):
                candidate=urljoin(final,anchor['href'])
                parsed=urlparse(candidate)
                if parsed.hostname!='jobs.baxter.com' or not parsed.path.startswith('/en/job/'):
                    continue
                label=anchor.get_text(' ',strip=True)
                if not location_matches(label,locations):continue
                lower=label.lower()
                if not any(word in lower for word in ('quality','engineer','hardware','r&d',
                                                      'research','product','systems','automation')):
                    continue
                if any(word in lower for word in ('sales','software','finance','recruit',
                                                  'accounts payable','marketing')):
                    continue
                if candidate not in seen:links.append(candidate)
            write('UPDATE api_calls SET response_count=?,http_status=200,completed_at=? WHERE id=?',
                  (len(links),utcnow(),call_id))
            for candidate in links:
                if len(seen)>=12:break
                seen.add(candidate)
                raw=cited_employer_vacancy(candidate,'Baxter','https://jobs.baxter.com',days,run_id)
                if raw:results.append(raw)
        except Exception as exc:
            write('UPDATE api_calls SET error=?,completed_at=? WHERE id=?',
                  (str(exc)[:250],utcnow(),call_id))
    return results
