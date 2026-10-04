from fastapi.testclient import TestClient

from app import db, search
from app.config import settings
from app.main import app
from test_workflow import sample_job


def test_owner_cost_view_is_private(test_env):
    original = settings.owner_password
    object.__setattr__(settings, 'owner_password', 'distinct-owner-password')
    try:
        with TestClient(app) as candidate:
            candidate.post('/login', data={'password': settings.app_password})
            assert candidate.get('/api-use').status_code == 403
            assert '/api-use' not in candidate.get('/').text
        db.write("INSERT INTO api_calls(provider,operation,request_json,estimated_cost_usd,started_at) VALUES('JSearch','search','{}',0.005,?)", (db.utcnow(),))
        with TestClient(app) as owner:
            owner.post('/login', data={'password': 'distinct-owner-password'})
            html = owner.get('/api-use').text
            assert owner.get('/api-use').status_code == 200
            assert '$0.0050' in html and 'JSearch' in html
            assert '/activity' in owner.get('/').text
    finally:
        object.__setattr__(settings, 'owner_password', original)


def test_repeat_search_recommends_next_new_role_only(test_env, monkeypatch):
    from app import direct_links, ats_feeds
    monkeypatch.setattr(ats_feeds, 'discover_ats_vacancies', lambda *args: [])
    original_key = settings.jsearch_api_key
    original_model_key = settings.openrouter_api_key
    original_requests = settings.jsearch_max_requests_per_run
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    object.__setattr__(settings, 'openrouter_api_key', '')
    object.__setattr__(settings, 'jsearch_max_requests_per_run', 3)
    calls = []
    checks = []
    phase = [1]
    monkeypatch.setattr(direct_links, 'discover_employer_vacancies', lambda *a, **k: [])
    monkeypatch.setattr(direct_links, 'resolve_job', lambda job, **kw: (checks.append(job['id']), dict(job, direct_status='verified', direct_url=job['apply_url']))[1])
    def fetch(*args):
        calls.append(args)
        jobs = [sample_job('R&D Director', 'Textile Lab', 'repeat')]
        if phase[0] >= 2:
            jobs.append(sample_job('R&D Director', 'Device Lab', 'new'))
        return jobs, None
    monkeypatch.setattr(search, 'fetch_page', fetch)
    try:
        first = search.run_search(kind='manual', roles='R&D Director', keywords='', locations='Chennai', work_types='Full-time', count=1)
        first_calls = len(calls)
        first_checks = len(checks)
        phase[0] = 2
        second = search.run_search(kind='manual', roles='R&D Director', keywords='', locations='Chennai', work_types='Full-time', count=1)
        assert len(calls) > first_calls
        assert len(checks) == first_checks + 1
        assert db.one('SELECT seen_before FROM search_results WHERE run_id=?', (first,))['seen_before'] == 0
        assert db.one('SELECT seen_before FROM search_results WHERE run_id=?', (second,))['seen_before'] == 0
        assert db.one('SELECT j.company FROM search_results r JOIN jobs j ON j.id=r.job_id WHERE r.run_id=?', (second,))['company'] == 'Device Lab'
        third = search.run_search(kind='manual', roles='R&D Director', keywords='', locations='Chennai', work_types='Full-time', count=1)
        assert db.one('SELECT COUNT(*) AS n FROM search_results WHERE run_id=?', (third,))['n'] == 0
        db.write("UPDATE jobs SET direct_status='verified',verification_version=3,direct_url=apply_url")
        with TestClient(app) as client:
            client.post('/login', data={'password': settings.app_password})
            html = client.get(f'/runs/{third}').text
            assert 'No new suitable company vacancies' in html
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)
        object.__setattr__(settings, 'openrouter_api_key', original_model_key)
        object.__setattr__(settings, 'jsearch_max_requests_per_run', original_requests)


def test_same_employer_vacancy_from_new_source_id_is_not_recommended_again(test_env, monkeypatch):
    from app import direct_links, ats_feeds
    monkeypatch.setattr(ats_feeds, 'discover_ats_vacancies', lambda *args: [])
    monkeypatch.setattr(direct_links, 'discover_employer_vacancies', lambda *a, **k: [])
    monkeypatch.setattr(direct_links, 'resolve_job', lambda job, **kw: dict(
        job, direct_status='verified', direct_url='https://employer.example/careers?id=123'))
    original_key = settings.jsearch_api_key
    original_model_key = settings.openrouter_api_key
    original_requests = settings.jsearch_max_requests_per_run
    object.__setattr__(settings, 'jsearch_api_key', 'test-key')
    object.__setattr__(settings, 'openrouter_api_key', '')
    object.__setattr__(settings, 'jsearch_max_requests_per_run', 1)
    source_id = ['first-source-id']
    def fetch(*args):
        return [sample_job('R&D Director', 'Textile Lab', source_id[0])], None
    monkeypatch.setattr(search, 'fetch_page', fetch)
    try:
        first = search.run_search(kind='manual', roles='R&D Director', keywords='', locations='Chennai', work_types='Full-time', count=1)
        # The verifier normally persists this URL. Simulate that persistence here.
        job_id = db.one('SELECT job_id FROM search_results WHERE run_id=?', (first,))['job_id']
        db.write("UPDATE jobs SET direct_url=?,direct_status='verified',verification_version=3 WHERE id=?",
                 ('https://employer.example/careers?id=123',job_id))
        source_id[0] = 'second-source-id'
        second = search.run_search(kind='manual', roles='R&D Director', keywords='', locations='Chennai', work_types='Full-time', count=1)
        assert db.one('SELECT COUNT(*) AS n FROM search_results WHERE run_id=?', (second,))['n'] == 0
    finally:
        object.__setattr__(settings, 'jsearch_api_key', original_key)
        object.__setattr__(settings, 'openrouter_api_key', original_model_key)
        object.__setattr__(settings, 'jsearch_max_requests_per_run', original_requests)
