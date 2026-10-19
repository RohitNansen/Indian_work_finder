from app import db, main, medical_crawl, search
from app.company_memory import medical_company_sites, seed_career_sites
from app.medical_directory import MEDICAL_COMPANIES
from app.config import settings
from conftest import csrf
from test_workflow import sample_job


def test_directory_is_large_unique_and_rotates(test_env):
    assert len(MEDICAL_COMPANIES) >= 50
    assert len({name.lower() for name, _, _ in MEDICAL_COMPANIES}) == len(MEDICAL_COMPANIES)
    assert all(url.startswith('https://') for _, url, _ in MEDICAL_COMPANIES)
    seed_career_sites()
    first = medical_company_sites(12)
    assert len(first) == 12
    for company in first:
        from app.company_memory import mark_company_scanned
        mark_company_scanned(company['id'])
    second = medical_company_sites(12)
    assert {x['name'] for x in first} & {x['name'] for x in second} == {'BD'}


def test_free_crawler_emits_only_verified_same_host_vacancies(test_env, monkeypatch):
    seed_career_sites()
    company = next(x for x in medical_company_sites(100) if x['name'] == 'Baxter')
    html = '<a href="/en/job/123">Head of Quality</a><a href="https://naukri.com/job/3">Head of Quality</a>'
    monkeypatch.setattr(medical_crawl, 'read_page', lambda url: ('https://jobs.baxter.com/en/', html))
    checked = []
    def verify(url, company, website, days, run_id):
        checked.append(url)
        return {'job_title': 'Head of Quality', 'employer_name': company}
    monkeypatch.setattr(medical_crawl, 'cited_employer_vacancy', verify)
    run_id = db.write("INSERT INTO search_runs(kind,started_at,parameters_json) VALUES('manual',?,'{}')", (db.utcnow(),))
    rows = medical_crawl.crawl_medical_companies([company], 30, run_id)
    assert len(rows) == 1
    assert checked == ['https://jobs.baxter.com/en/job/123']
    assert db.one('SELECT last_scanned_at FROM companies WHERE id=?', (company['id'],))[0]


def test_medical_focus_is_saved_from_search_form(client, monkeypatch):
    original_key = settings.jsearch_api_key
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    calls = []
    monkeypatch.setattr(main, 'run_search', lambda **args: calls.append(args))
    try:
        response = client.post('/search', data={
            '_csrf': csrf(client), 'roles': 'Head of Quality', 'locations': 'Chennai',
            'work_types': 'Full-time', 'count': '10', 'days_recent': '30',
            'focus_medical': '1',
        }, follow_redirects=False)
        assert response.status_code == 303
        assert calls[0]['focus_medical'] is True
        assert db.one('SELECT focus_medical FROM profile WHERE id=1')[0] == 1
        assert 'checked' in client.get('/').text.split('id="focus_medical"', 1)[1].split('>', 1)[0]
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)


def test_version_four_verified_result_remains_visible(client):
    job = search.save_job(sample_job('Head of Quality', 'Medical Lab', 'version-4'))
    db.write("UPDATE jobs SET direct_status='verified',verification_version=4,direct_url=apply_url WHERE id=?", (job['id'],))
    run_id = db.write("INSERT INTO search_runs(kind,started_at,parameters_json,status,progress_json) VALUES('manual',?,'{}','complete','{}')", (db.utcnow(),))
    db.write("INSERT INTO search_results(run_id,job_id,rank,internal_score) VALUES(?,?,1,80)", (run_id,job['id']))
    assert 'Head of Quality' in client.get(f'/runs/{run_id}').text


def test_focused_search_checks_medical_directory_before_other_sources(test_env, monkeypatch):
    from app import ats_feeds, company_memory, direct_links, employer_indexes
    original_key = settings.jsearch_api_key
    original_apify = settings.apify_token
    original_model = settings.openrouter_api_key
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    object.__setattr__(settings, 'apify_token', '')
    object.__setattr__(settings, 'openrouter_api_key', '')
    order = []
    monkeypatch.setattr(search, 'jsearch_has_capacity', lambda: False)
    monkeypatch.setattr(company_memory, 'medical_company_sites', lambda limit: [{'id': 1, 'name': 'Medical Lab', 'website_hint': 'https://example.com/careers'}])
    monkeypatch.setattr(medical_crawl, 'crawl_medical_companies', lambda *args, **kwargs: (order.append('medical'), [])[1])
    monkeypatch.setattr(ats_feeds, 'discover_ats_vacancies', lambda *args: (order.append('boards'), [])[1])
    monkeypatch.setattr(employer_indexes, 'discover_baxter_vacancies', lambda *args: [])
    monkeypatch.setattr(employer_indexes, 'discover_radiometer_sitemap_vacancies', lambda *args: [])
    monkeypatch.setattr(company_memory, 'candidate_company_sites', lambda limit: [])
    monkeypatch.setattr(company_memory, 'watched_company_sites', lambda limit: [])
    monkeypatch.setattr(direct_links, 'discover_employer_vacancies', lambda *args: [])
    try:
        run_id = search.run_search(kind='manual', roles='Head of Quality', keywords='',
                                   locations='Chennai', work_types='Full-time', count=1,
                                   focus_medical=True)
        assert order[:2] == ['medical', 'boards']
        assert db.one('SELECT status FROM search_runs WHERE id=?', (run_id,))[0] in {'complete', 'partial_quota'}
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)
        object.__setattr__(settings, 'apify_token', original_apify)
        object.__setattr__(settings, 'openrouter_api_key', original_model)
import json



def test_bd_legal_employer_name_is_accepted_only_on_bd_vacancy(test_env, monkeypatch):
    from app import direct_links
    posting = {
        '@type': 'JobPosting', 'title': 'Lead Design Engineer',
        'hiringOrganization': {'name': 'Becton Dickinson & Company'},
        'datePosted': db.utcnow(),
        'description': 'Lead medical device design and validation, review quality systems, '
                       'and coordinate multidisciplinary product development teams in Bengaluru.' * 2,
        'jobLocation': {'address': {'addressLocality': 'Bengaluru',
                                   'addressRegion': 'Karnataka', 'addressCountry': 'India'}},
    }
    html = '<script type="application/ld+json">' + json.dumps(posting) + '</script>'
    monkeypatch.setattr(direct_links, 'read_page', lambda url: (url, html))
    good = direct_links.cited_employer_vacancy('https://jobs.bd.com/en/job/bengaluru/123',
                                                'BD', 'https://jobs.bd.com/en/', 30)
    bad = direct_links.cited_employer_vacancy('https://other.example/job/123',
                                               'BD', 'https://jobs.bd.com/en/', 30)
    assert good and good['job_title'] == 'Lead Design Engineer'
    assert bad is None
