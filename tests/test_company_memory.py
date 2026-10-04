from app.company_memory import (remember_company, remember_verified_vacancy,
                                verified_boards, verified_company)
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
