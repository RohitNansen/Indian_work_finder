from app.company_memory import (candidate_company_sites, mark_company_scanned,
                                seed_career_sites, remember_verified_vacancy)
from app.direct_links import discover_watched_company_vacancies
from app.matching import location_matches, search_places


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
