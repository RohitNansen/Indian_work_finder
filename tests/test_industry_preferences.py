import json

from app import db, main, search
from app.config import settings
from app.industries import DEFAULT_INDUSTRIES, industry_priority
from app.naukri import query_choice
from test_workflow import sample_job
from conftest import csrf


def test_medical_queries_and_order_precede_textiles_then_broader_roles():
    preferences = list(DEFAULT_INDUSTRIES)
    queue = list(search.query_queue(['R&D Director', 'Head of Quality'],
                                    ['Chennai', 'Bengaluru'], [], preferences))
    assert all('medical' in query.lower() or 'medtech' in query.lower()
               or 'life sciences' in query.lower() or 'pharmaceutical' in query.lower()
               for query, _, _ in queue[:6])
    assert all('textile' in query.lower() for query, _, _ in queue[6:8])
    assert any('R&D Director' in query for query, _, _ in queue[8:])
    assert query_choice(['Head of Quality'], ['Chennai'], 1, preferences) == (
        'medical device quality head', 'Chennai')

    def job(description):
        return {'title': 'R&D Director', 'company': 'Example', 'description': description}
    assert industry_priority(job('Medical device design and regulatory quality'), preferences) == 0
    assert industry_priority(job('Textile machinery and spinning controls'), preferences) == 1
    assert industry_priority(job('Industrial power converters'), preferences) == 2


def test_industry_choice_is_saved_and_sent_to_search(client, monkeypatch):
    original_key = settings.jsearch_api_key
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    calls = []
    monkeypatch.setattr(main, 'run_search', lambda **args: calls.append(args))
    try:
        assert 'Industries you prefer' in client.get('/').text
        response = client.post('/search', data={
            '_csrf': csrf(client), 'roles': 'Head of Quality', 'keywords': '',
            'locations': 'Chennai', 'work_types': 'Full-time',
            'industries': 'Medical devices, Textile machinery',
            'count': '10', 'days_recent': '30',
        }, follow_redirects=False)
        assert response.status_code == 303
        assert db.one('SELECT industry_preferences FROM profile WHERE id=1')[0] == (
            'Medical devices, Textile machinery')
        assert calls[0]['industries'] == 'Medical devices, Textile machinery'
        assert json.loads(db.one('SELECT parameters_json FROM search_runs')[0])['industries'] == (
            'Medical devices, Textile machinery')
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)


def test_search_prefers_medical_but_keeps_other_suitable_jobs(test_env, monkeypatch):
    from app import ats_feeds, direct_links, employer_indexes

    original_key = settings.jsearch_api_key
    original_model_key = settings.openrouter_api_key
    original_requests = settings.jsearch_max_requests_per_run
    original_apify = settings.apify_token
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    object.__setattr__(settings, 'openrouter_api_key', '')
    object.__setattr__(settings, 'apify_token', '')
    object.__setattr__(settings, 'jsearch_max_requests_per_run', 3)
    entries = [
        {**sample_job('R&D Director', 'Industrial Lab', 'industrial'),
         'job_description': 'Lead industrial electrical product design and engineering.'},
        {**sample_job('R&D Director', 'Medical Lab', 'medical'),
         'job_description': 'Lead medical device product design and quality engineering.'},
    ]
    queries = []
    monkeypatch.setattr(ats_feeds, 'discover_ats_vacancies', lambda *args: entries)
    monkeypatch.setattr(employer_indexes, 'discover_baxter_vacancies', lambda *args: [])
    monkeypatch.setattr(employer_indexes, 'discover_radiometer_sitemap_vacancies', lambda *args: [])
    monkeypatch.setattr(direct_links, 'resolve_job', lambda job, **kw: dict(
        job, direct_status='verified', direct_url=job['apply_url']))
    monkeypatch.setattr(direct_links, 'discover_employer_vacancies', lambda *args: [])
    monkeypatch.setattr(search, 'fetch_page', lambda _, query, *args: (queries.append(query), ([], None))[1])
    try:
        run = search.run_search(kind='manual', roles='R&D Director', keywords='',
                                locations='Chennai', work_types='Full-time', count=1)
        selected = db.one('SELECT j.company FROM search_results r JOIN jobs j ON j.id=r.job_id '
                          'WHERE r.run_id=?', (run,))
        assert selected['company'] == 'Medical Lab'
        assert len(queries) == 3
        assert all('medical' in query.lower() or 'medtech' in query.lower()
                   for query in queries)

        entries[:] = entries[:1]
        run = search.run_search(kind='manual', roles='R&D Director', keywords='',
                                locations='Chennai', work_types='Full-time', count=1)
        # Already shown jobs are excluded, so add another suitable industry.
        assert db.one('SELECT COUNT(*) AS n FROM search_results WHERE run_id=?', (run,))['n'] == 1
        assert db.one('SELECT j.company FROM search_results r JOIN jobs j ON j.id=r.job_id '
                      'WHERE r.run_id=?', (run,))['company'] == 'Industrial Lab'
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)
        object.__setattr__(settings, 'openrouter_api_key', original_model_key)
        object.__setattr__(settings, 'jsearch_max_requests_per_run', original_requests)
        object.__setattr__(settings, 'apify_token', original_apify)
