from app.company_memory import (remember_company, remember_verified_vacancy,
                                verified_boards, verified_company, watched_company_sites)
from app.db import one
from app.search import normalized_link


def test_company_memory_keeps_unverified_hints_separate(test_env):
    remember_company("Acme Pvt Ltd", "JSearch", "first", "https://acme.example/careers")
    remember_company("Acme Private Limited", "Apify Naukri", "second", None)
    remember_company("Acme Pvt Ltd", "JSearch", "first", None)
    assert one("SELECT COUNT(*) AS n FROM companies")["n"] == 1
    assert one("SELECT COUNT(*) AS n FROM company_observations")["n"] == 2
    assert verified_company("Acme") is None
    assert verified_boards() == []


def test_verified_ats_board_can_be_reused(test_env):
    remember_company("Dozee", "JSearch", "listing", "https://www.dozee.health")
    remember_verified_vacancy("Dozee", "https://jobs.lever.co/dozee/abc123", "https://www.dozee.health")
    company = verified_company("Dozee")
    assert company["career_page"] == "https://jobs.lever.co/dozee"
    assert company["verified_job_url"] == "https://jobs.lever.co/dozee/abc123"
    assert ("Lever", "dozee", "Dozee", "https://www.dozee.health") in verified_boards()


def test_vacancy_identity_keeps_job_id_but_drops_tracking():
    assert normalized_link("https://example.com/careers?id=123&utm_source=naukri") == normalized_link(
        "https://www.example.com/careers?utm_source=jsearch&id=123")
    assert normalized_link("https://example.com/careers?id=123") != normalized_link(
        "https://example.com/careers?id=456")


def test_company_page_is_owner_only(client):
    response = client.get("/companies")
    assert response.status_code == 403


def test_watchlist_rotates_and_keeps_only_employer_urls(test_env, monkeypatch):
    from app import ai
    from app.direct_links import discover_watched_company_vacancies
    for name in ("Alpha Energy", "Beta Devices"):
        remember_verified_vacancy(name, f"https://{name.split()[0].lower()}.example/careers/job/123")
    first = watched_company_sites(1)[0]
    domain = first['verified_website'].split('//')[1]
    monkeypatch.setattr(ai, 'ask_json', lambda **kwargs: {'jobs': [
        {'title':'Head of Quality','location':'Chennai','url':f'https://{domain}/careers/job/456'},
        {'title':'Head of Quality','location':'Chennai','url':'https://unrelated.example/job/456'},
    ]})
    rows = discover_watched_company_vacancies(['Head of Quality'], ['Chennai'], 30, first)
    assert len(rows) == 1
    assert rows[0]['job_apply_link'].startswith(f'https://{domain}/')
    assert watched_company_sites(1)[0]['id'] != first['id']
