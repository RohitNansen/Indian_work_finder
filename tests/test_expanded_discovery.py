from app.company_memory import (candidate_company_sites, mark_company_scanned,
                                seed_career_sites, remember_verified_vacancy)
from app.direct_links import (discover_watched_company_vacancies, cited_employer_vacancy,
                              vacancy_details)
from app.matching import location_matches, search_places
from app.search import plausible_physical_role, jsearch_has_capacity
from datetime import datetime,timezone,timedelta
import json


def test_south_india_is_a_real_location_filter():
    assert search_places(['Chennai', 'South India', 'Remote India'])[:3] == [
        'Chennai', 'Hyderabad', 'Kochi']
    assert location_matches('Hyderabad, Telangana, India', ['South India'])
    assert location_matches('Kochi, Kerala, India', ['South India'])
    assert location_matches('Bangalore, Karnataka, India', ['South India'])
    assert not location_matches('Mumbai, Maharashtra, India', ['South India'])
    assert not location_matches('Remote, India', ['South India'], remote=True)


def test_saved_medical_company_hints_rotate_without_becoming_verified(test_env):
    seed_career_sites()
    first=candidate_company_sites(2)
    assert first[0]['name']=='Baxter'
    assert first[0]['verified'] is False
    assert first[0]['website_hint'].startswith('https://jobs.baxter.com')
    mark_company_scanned(first[0]['id'])
    assert candidate_company_sites(1)[0]['name'] != 'Baxter'
    remember_verified_vacancy('Baxter',
        'https://jobs.baxter.com/en/job/bengaluru/senior-engineer-quality-assurance/152/101892175664')
    assert all(company['name']!='Baxter' for company in candidate_company_sites(20))


def test_unverified_career_hint_searches_domain_but_does_not_trust_it(test_env, monkeypatch):
    from app import ai
    seed_career_sites()
    candidate=candidate_company_sites(1)[0]
    recorded=[]
    def answer(**kwargs):
        recorded.append(kwargs)
        return {'jobs':[{'title':'Head of Quality','location':'Bengaluru, Karnataka, India',
                         'url':'https://jobs.baxter.com/en/job/bengaluru/head-of-quality/152/123'},
                        {'title':'Head of Quality','location':'Bengaluru, Karnataka, India',
                         'url':'https://www.naukri.com/job-listings-123'}]}
    monkeypatch.setattr(ai,'ask_json',answer)
    jobs=discover_watched_company_vacancies(['Head of Quality'],['Bengaluru'],30,candidate)
    assert len(jobs)==1
    assert jobs[0]['employer_website']==''
    assert jobs[0]['source']=='Saved company career hint'
    assert recorded[0]['web_search']['include_domains']==['jobs.baxter.com']


def test_cited_official_posting_is_recovered_with_its_own_date_and_location(test_env, monkeypatch):
    from app import direct_links
    url='https://jobs.baxter.com/en/job/bengaluru/head-of-quality/152/123'
    posting={'@type':'JobPosting','title':'Head of Quality',
             'hiringOrganization':{'name':'Baxter'},
             'datePosted':(datetime.now(timezone.utc)-timedelta(days=2)).date().isoformat(),
             'jobLocation':{'address':{'addressLocality':'Bengaluru',
                                       'addressRegion':'Karnataka','addressCountry':'IN'}},
             'description':'Lead medical device design quality, verification and product development. '*3}
    html='<html><h1>Head of Quality</h1><script type="application/ld+json">'+json.dumps(posting)+'</script></html>'
    monkeypatch.setattr(direct_links,'read_page',lambda _: (url,html))
    job=cited_employer_vacancy(url,'Baxter','',30)
    assert job['job_title']=='Head of Quality'
    assert job['job_location']=='Bengaluru, Karnataka, IN'
    assert job['job_posted_at_datetime_utc']==posting['datePosted']
    assert cited_employer_vacancy(url,'Another Company','',30) is None
    posting['datePosted']=(datetime.now(timezone.utc)-timedelta(days=70)).date().isoformat()
    html='<html><script type="application/ld+json">'+json.dumps(posting)+'</script></html>'
    assert cited_employer_vacancy(url,'Baxter','',30) is None


def test_obvious_software_and_sales_roles_do_not_use_vacancy_checks():
    assert not plausible_physical_role({'title':'Senior Software Quality Engineer - Selenium'})
    assert not plausible_physical_role({'title':'Head of Sales'})
    assert plausible_physical_role({'title':'Medical Device Hardware Engineering Manager'})
    assert plausible_physical_role({'title':'Head of Quality'})


def test_baxter_index_scans_only_location_and_topic_jobs(test_env,monkeypatch):
    from app import employer_indexes
    calls=[]
    html='''<a href="/en/job/bengaluru/senior-quality/152/1">Senior Engineer, Quality Bengaluru, Karnataka</a>
            <a href="/en/job/mumbai/senior-quality/152/2">Senior Engineer, Quality Mumbai, Maharashtra</a>
            <a href="/en/job/bengaluru/sales/152/3">Head of Sales Bengaluru, Karnataka</a>'''
    monkeypatch.setattr(employer_indexes,'read_page',lambda url:(url,html))
    def official(url,*args):
        calls.append(url)
        return {'job_uid':url,'job_title':'Senior Engineer, Quality'}
    monkeypatch.setattr(employer_indexes,'cited_employer_vacancy',official)
    rows=employer_indexes.discover_baxter_vacancies(['South India'],30)
    assert len(rows)==1
    assert calls==['https://jobs.baxter.com/en/job/bengaluru/senior-quality/152/1']


def test_danaher_parent_site_requires_named_subsidiary(test_env,monkeypatch):
    from app import direct_links
    url='https://jobs.danaher.com/global/en/job/R1318481'
    posting={'@type':'JobPosting','title':'Engineering Manager, Verification and Validation',
             'hiringOrganization':{'name':'Danaher'},
             'datePosted':(datetime.now(timezone.utc)-timedelta(days=2)).date().isoformat(),
             'jobLocation':{'address':{'addressLocality':'Bengaluru','addressCountry':'IN'}},
             'description':'Radiometer develops clinical diagnostic devices. Lead verification and validation of medical products. '*3}
    html='<h1>Engineering Manager, Verification and Validation</h1><script type="application/ld+json">'+json.dumps(posting)+'</script>'
    job={'title':posting['title'],'company':'Radiometer','company_url':'https://radiometer.com',
         'location':'Bengaluru'}
    assert vacancy_details(url,html,job)[0]
    monkeypatch.setattr(direct_links,'read_page',lambda _: (url,html))
    assert cited_employer_vacancy(url,'Radiometer','',30)['employer_name']=='Radiometer'
    posting['description']=posting['description'].replace('Radiometer','Acme')
    html='<h1>'+posting['title']+'</h1><script type="application/ld+json">'+json.dumps(posting)+'</script>'
    assert not vacancy_details(url,html,job)[0]
    assert cited_employer_vacancy(url,'Radiometer','',30) is None


def test_jsearch_usage_skips_exhausted_free_plan(test_env,monkeypatch):
    from app import search
    from app.config import settings
    object.__setattr__(settings,'jsearch_api_key','dummy')
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'data':{'status':'exceeded','quotas':[
            {'name':'Requests','used':202,'limit':200}]}}
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr(search.httpx,'Client',Client)
    assert not jsearch_has_capacity()


def test_radiometer_sitemap_rotates_official_vacancy_checks(test_env,monkeypatch):
    from app import employer_indexes
    base='https://jobs.danaher.com/global/en/job/'
    xml=('<urlset><url><loc>'+base+'DANAGLOBALR1318481EXTERNAL/Engineering-Manager-Verification-Validation'
         '</loc></url><url><loc>'+base+'DANAGLOBALR1318482EXTERNAL/Senior-Quality-Manager'
         '</loc></url></urlset>').encode()
    class Response:
        content=xml
        def raise_for_status(self):pass
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,*args):return Response()
    calls=[]
    monkeypatch.setattr(employer_indexes.httpx,'Client',Client)
    monkeypatch.setattr(employer_indexes,'cited_employer_vacancy',
                        lambda url,*args:(calls.append(url) or {'job_uid':url}))
    assert len(employer_indexes.discover_radiometer_sitemap_vacancies(30))==2
    assert len(set(calls))==2
    calls.clear()
    assert len(employer_indexes.discover_radiometer_sitemap_vacancies(30))==2
    assert len(calls)==2
