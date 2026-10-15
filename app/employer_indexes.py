"""Bounded public employer career-index adapters, starting with Baxter India."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

from .db import all_rows, utcnow, write
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


def discover_radiometer_sitemap_vacancies(days: int,
                                           run_id: int | None = None) -> list[dict]:
    """Rotate through senior roles in Danaher's public job sitemaps, verifying each page."""
    urls={}
    for number in (1,2,3):
        sitemap=f'https://jobs.danaher.com/global/en/sitemap{number}.xml'
        call_id=write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) '
                      'VALUES(?,?,?,?,?)',(run_id,'Employer website','danaher_sitemap',
                                        json.dumps({'url':sitemap}),utcnow()))
        try:
            with httpx.Client(timeout=12,trust_env=False) as client:
                response=client.get(sitemap)
            response.raise_for_status()
            if len(response.content)>500_000:raise ValueError('Sitemap too large')
            root=ET.fromstring(response.content)
            for node in root.iter():
                if not node.tag.endswith('loc') or not node.text:continue
                url=node.text.strip()
                parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.hostname!='jobs.danaher.com' or not parsed.path.startswith('/global/en/job/'):
                    continue
                match=re.search(r'R\d{6,}',parsed.path)
                if match:urls.setdefault(match.group(),url)
            write('UPDATE api_calls SET response_count=?,http_status=200,completed_at=? WHERE id=?',
                  (len(urls),utcnow(),call_id))
        except Exception as exc:
            write('UPDATE api_calls SET error=?,completed_at=? WHERE id=?',
                  (str(exc)[:250],utcnow(),call_id))
    priorities=(('engineering-manager',30),('quality-manager',28),('manager-quality',28),
                ('r-d-manager',26),('hardware-engineering',22),('verification-validation',20),
                ('quality',16),('r-d',13),('hardware',12),('product-development',11),
                ('engineering',9),('director',8),('manager',7),('lead',5),('senior',3))
    ranked=[]
    for job_id,url in urls.items():
        title=url.rsplit('/',1)[-1].lower()
        if not any(term in title for term in ('quality','verification','validation','hardware',
                                              'engineering','product-development','r-d','research')):
            continue
        if not any(term in title for term in ('manager','director','head','principal',
                                              'senior','sr-','lead','architect')):
            continue
        if any(term in title for term in ('sales','marketing','finance','data-',
                                           'cloud','account','commercial','cyber',
                                           'security','software','hr-','customer-success')):
            continue
        score=sum(weight for term,weight in priorities if term in title)
        ranked.append((job_id,url,score))
    history={}
    for row in all_rows("SELECT request_json,started_at FROM api_calls WHERE "
                        "operation='radiometer_sitemap_vacancy' ORDER BY started_at"):
        try:
            payload=json.loads(row['request_json'])
            history[payload['job_id']]=row['started_at']
        except (ValueError,KeyError,TypeError):continue
    ranked.sort(key=lambda item:(item[0] in history,history.get(item[0],''),
                                 -item[2],item[1]))
    results=[]
    for job_id,url,_ in ranked[:12]:
        write('INSERT INTO api_calls(run_id,provider,operation,request_json,started_at,completed_at) '
              'VALUES(?,?,?,?,?,?)',(run_id,'Employer website','radiometer_sitemap_vacancy',
                                json.dumps({'job_id':job_id,'url':url}),utcnow(),utcnow()))
        raw=cited_employer_vacancy(url,'Radiometer','',days,run_id)
        if raw:results.append(raw)
    return results
