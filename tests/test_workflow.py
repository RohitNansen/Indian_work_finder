import json
import pytest
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from docx import Document
from fastapi.testclient import TestClient

from app import db, search
from app.alerts import parse_recipients, run_due_alerts
from app.config import settings
from app.main import app
from app.evidence import evidence_matches, store_evidence
from app.resume import apply_changes, export_variant
from conftest import csrf


def sample_job(title, company, suffix):
    return {
        "job_id": suffix, "job_title": title, "employer_name": company,
        "job_location": "Chennai, Tamil Nadu, India", "job_country": "IN",
        "job_employment_type": "Full-time", "job_description":
        "Lead research and development, quality systems and product improvement.",
        "job_apply_link": f"https://example.com/apply/{suffix}",
        "job_posted_at_datetime_utc": "2026-09-24T08:00:00Z",
    }


def test_search_deduplicates_and_tracks_click_separately(client, monkeypatch):
    object.__setattr__(settings, "jsearch_api_key", "test-key")
    object.__setattr__(settings, "openrouter_api_key", "")
    monkeypatch.setattr(search, "fetch_page", lambda *args: (
        [sample_job("R&D Director", "Textile Lab", "a"),
         sample_job("R&D Director", "Textile Lab", "a"),
         sample_job("Junior Assistant", "Other", "b")], None))
    run_id = search.run_search(kind="manual", roles="R&D Director", keywords="quality",
                               locations="Chennai", work_types="Full-time", count=10)
    rows = db.all_rows("SELECT j.title,r.rank FROM search_results r JOIN jobs j ON j.id=r.job_id "
                       "WHERE r.run_id=? ORDER BY r.rank", (run_id,))
    assert len(rows) == 2
    assert rows[0]["title"] == "R&D Director"
    job_id = db.one("SELECT id FROM jobs WHERE title='R&D Director'")["id"]
    response = client.get(f"/out/{job_id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "https://example.com/apply/a"
    assert "R&amp;D Director" in client.get("/my-jobs").text
    assert db.one("SELECT status FROM job_status WHERE job_id=?", (job_id,)) is None
    client.post(f"/jobs/{job_id}/status", data={"_csrf": csrf(client), "status": "Applied"})
    assert db.one("SELECT status FROM job_status WHERE job_id=?", (job_id,))["status"] == "Applied"


def test_answer_becomes_editable_profile_fact(client):
    raw = sample_job("Research Advisor", "Life Lab", "c")
    job = search.save_job(raw)
    qid = db.write("INSERT INTO questions(job_id,question,requirement,created_at) VALUES(?,?,?,?)",
                   (job["id"], "Have you led pilot production?", "Led pilot production",
                    db.utcnow()))
    response = client.post(f"/jobs/{job['id']}/questions/{qid}",
                           data={"_csrf": csrf(client), "answer": "Yes",
                                 "detail": "At HCL for five years"}, follow_redirects=False)
    assert response.status_code == 303
    assert "Led pilot production" in client.get("/profile").text
    client.post(f"/jobs/{job['id']}/questions/{qid}",
                data={"_csrf": csrf(client), "answer": "No", "detail": ""})
    assert db.one("SELECT COUNT(*) AS n FROM facts WHERE active=1")["n"] == 0


def test_resume_export_requires_exact_existing_wording(test_env):
    source = test_env / "resume.docx"
    document = Document()
    document.add_paragraph("Led textile research programs.")
    document.save(source)
    original = "Led textile research programs."
    changes = [{"original": original,
                "replacement": "Led textile research programs and pilot trials."}]
    docx_path, pdf_path = export_variant(source, original, changes,
                                         "A Person", "Example Company", 1)
    assert docx_path.name == "A Person - Example Company.docx"
    assert pdf_path.exists() and pdf_path.stat().st_size > 100
    assert "pilot trials" in Document(docx_path).paragraphs[0].text
    try:
        apply_changes(original, [{"original": "Experience never written", "replacement": "X"}])
        assert False, "An unsupported edit should fail"
    except ValueError:
        pass


def test_alert_due_at_ist_once_per_day(test_env, monkeypatch):
    provider_fields = ("jsearch_api_key", "openrouter_api_key", "resend_api_key", "email_from")
    originals = {field: getattr(settings, field) for field in provider_fields}
    for field in provider_fields:
        object.__setattr__(settings, field, "test-value")
    aid = db.write(
        "INSERT INTO alerts(name,email,role_labels,keywords,locations,work_types,time_ist,count,"
        "days_recent,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("Daily", "someone@example.com", "R&D", "", "Chennai", "Full-time", "08:00", 5, 7,
         db.utcnow()),
    )
    sent = []
    from app import alerts
    def fake_run(alert):
        sent.append(alert["id"])
        db.write("UPDATE alerts SET last_sent_date_ist=? WHERE id=?", ("2026-09-25", aid))
        return 3
    monkeypatch.setattr(alerts, "run_alert", fake_run)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    try:
        assert run_due_alerts(now) == [(aid, "sent 3")]
        assert run_due_alerts(now) == []
        assert sent == [aid]
    finally:
        for field, value in originals.items():
            object.__setattr__(settings, field, value)


def test_failed_daily_alert_does_not_repeat_paid_searches(test_env, monkeypatch):
    from app import alerts
    aid = db.write(
        "INSERT INTO alerts(name,email,role_labels,keywords,locations,work_types,time_ist,count,"
        "days_recent,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("Daily", "one@example.com", "R&D", "", "Chennai", "", "08:00", 5, 7, db.utcnow()),
    )
    provider_fields = ("jsearch_api_key", "openrouter_api_key", "resend_api_key", "email_from")
    originals = {field: getattr(settings, field) for field in provider_fields}
    for field in provider_fields:
        object.__setattr__(settings, field, "test-value")
    attempts = []

    def fail(alert):
        attempts.append(alert["id"])
        raise RuntimeError("mail unavailable")

    monkeypatch.setattr(alerts, "run_alert", fail)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    try:
        assert run_due_alerts(now) == [(aid, "failed: mail unavailable")]
        assert run_due_alerts(now) == []
        assert attempts == [aid]
    finally:
        for field, value in originals.items():
            object.__setattr__(settings, field, value)


def test_alert_recipients_are_editable_and_validated(client):
    recipients = "one@example.com, two@example.org"
    data = {"_csrf": csrf(client), "name": "Daily research jobs", "email": recipients,
            "roles": "R&D Head", "keywords": "quality", "locations": "Chennai",
            "work_types": "Consulting", "time_ist": "08:00", "count": "5",
            "days_recent": "7"}
    assert client.post("/alerts", data=data).status_code == 200
    saved = db.one("SELECT id,email FROM alerts ORDER BY id DESC LIMIT 1")
    assert saved["email"] == recipients
    assert recipients in client.get(f"/alerts/{saved['id']}/edit").text
    data["email"] = "one@example.com"
    client.post(f"/alerts/{saved['id']}/edit", data=data)
    assert db.one("SELECT email FROM alerts WHERE id=?", (saved["id"],))["email"] == data["email"]
    data["email"] = "not an email, two@example.org"
    assert client.post(f"/alerts/{saved['id']}/edit", data=data).status_code == 400
    assert parse_recipients(recipients) == ["one@example.com", "two@example.org"]
    with pytest.raises(ValueError, match="duplicate"):
        parse_recipients("one@example.com, ONE@example.com")


def test_email_request_includes_both_alert_recipients(test_env, monkeypatch):
    from app import alerts
    sent = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "sent-id"}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            sent.append(kwargs["json"]["to"])
            return Response()

    old_key, old_from = settings.resend_api_key, settings.email_from
    object.__setattr__(settings, "resend_api_key", "test-key")
    object.__setattr__(settings, "email_from", "sender@example.com")
    monkeypatch.setattr(alerts.httpx, "Client", Client)
    try:
        assert alerts.send_email(parse_recipients(
            "one@example.com, two@example.org"), "Jobs", "Body") == "sent-id"
        assert sent == [["one@example.com", "two@example.org"]]
    finally:
        object.__setattr__(settings, "resend_api_key", old_key)
        object.__setattr__(settings, "email_from", old_from)


def test_gmail_sender_uses_tls_and_both_recipients(test_env, monkeypatch):
    from app import alerts
    actions = []

    class SMTP:
        def __init__(self, host, port, timeout):
            actions.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, context):
            actions.append(("tls", bool(context)))

        def login(self, username, password):
            actions.append(("login", username, password))

        def send_message(self, message, from_addr, to_addrs):
            actions.append(("send", from_addr, to_addrs, message["To"]))

    old = (settings.gmail_app_password, settings.email_from, settings.resend_api_key)
    object.__setattr__(settings, "gmail_app_password", "test-app-password")
    object.__setattr__(settings, "email_from", "sender@gmail.com")
    object.__setattr__(settings, "resend_api_key", "")
    monkeypatch.setattr(alerts.smtplib, "SMTP", SMTP)
    try:
        assert alerts.email_ready()
        assert alerts.send_email(["one@example.com", "two@example.org"],
                                 "Jobs", "<p>New jobs</p>").startswith("<")
        assert actions[0] == ("connect", "smtp.gmail.com", 587, 30)
        assert actions[1] == ("tls", True)
        assert actions[2] == ("login", "sender@gmail.com", "test-app-password")
        assert actions[3] == ("send", "sender@gmail.com",
                              ["one@example.com", "two@example.org"],
                              "one@example.com, two@example.org")
    finally:
        object.__setattr__(settings, "gmail_app_password", old[0])
        object.__setattr__(settings, "email_from", old[1])
        object.__setattr__(settings, "resend_api_key", old[2])


def test_resume_evidence_keeps_employer_and_role_context(test_env):
    rid = db.write("INSERT INTO resumes(filename,stored_path,file_type,resume_text,style_sample,uploaded_at) "
                   "VALUES(?,?,?,?,?,?)", ("example.pdf", "private.pdf", ".pdf", "text", "", db.utcnow()))
    text = ("PROFESSIONAL EXPERIENCE\nExample Engineering Ltd.\n"
            "Director - R&D | Chennai | 2000 - 2026\n"
            "• Led robotic medical device product development.\n"
            "• Built and led a team of 30 engineers.\n"
            "EDUCATION & PROFESSIONAL TRAINING\n• MS in Electronics\n")
    assert store_evidence(rid, text) == 3
    rows = db.all_rows("SELECT * FROM resume_evidence ORDER BY id")
    assert rows[0]["employer"] == "Example Engineering Ltd."
    assert "Director - R&D" in rows[0]["role"]
    assert rows[2]["section"] == "education"
    matches = evidence_matches({"title": "Medical Device R&D Head",
                                 "description": "Lead robotics product development"}, rows)
    assert matches and matches[0][1]["id"] == rows[0]["id"]


def test_rejects_explicitly_inactive_or_expired_jobs():
    raw = sample_job("R&D Head", "Example", "inactive")
    raw["job_is_active"] = False
    assert not search.acceptable_job(raw)
    raw["job_is_active"] = True
    raw["job_offer_expiration_datetime_utc"] = "2020-01-01T00:00:00Z"
    assert not search.acceptable_job(raw)


def test_role_search_includes_selected_keywords_and_rotates_places():
    queue = list(search.query_queue(["Electrical R&D Head", "Quality Head"],
                                    ["Chennai", "Bengaluru", "Remote India"],
                                    ["Electrical design", "Quality management"]))
    assert any("Electrical design" in item[0] for item in queue)
    assert any("Quality management" in item[0] for item in queue)
    assert {place for place in ("Chennai", "Bengaluru", "remote in India")
            if any(place in query for query, _, _ in queue)} == {"Chennai", "Bengaluru", "remote in India"}


def test_exhausted_job_quota_does_not_report_zero_suitable_jobs(test_env, monkeypatch):
    object.__setattr__(settings, "jsearch_api_key", "test-key")
    monkeypatch.setattr(search, "fetch_page", lambda *args: (_ for _ in ()).throw(search.QuotaExceeded("limit")))
    with pytest.raises(RuntimeError, match="quota reached"):
        search.run_search(kind="manual", roles="R&D Head", keywords="",
                          locations="Chennai", work_types="", count=5)
    run = db.one("SELECT status,error FROM search_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "failed"


def test_existing_domain_path_prefix(test_env):
    original_path = settings.app_base_path
    original_url = settings.app_base_url
    object.__setattr__(settings, "app_base_path", "/job-finder")
    object.__setattr__(settings, "app_base_url", "https://hithanis.com/job-finder")
    try:
        with TestClient(app) as client:
            assert client.get("/", follow_redirects=False).headers["location"] == "/job-finder/login"
            assert 'action="/job-finder/login"' in client.get("/login").text
            response = client.post("/login", data={"password": "test-password-long-enough"},
                                   follow_redirects=False)
            assert response.headers["location"] == "/job-finder/"
            assert "Path=/job-finder" in response.headers["set-cookie"]
    finally:
        object.__setattr__(settings, "app_base_path", original_path)
        object.__setattr__(settings, "app_base_url", original_url)


def test_quota_alerts_once_and_retries_failure(test_env, monkeypatch):
    from app import quota
    object.__setattr__(settings, "quota_alert_email", "owner@example.com")
    sent = []
    monkeypatch.setattr(quota, "send_email", lambda *args: sent.append(args) or "message-id")
    quota.evaluate("JSearch", "Requests", "2026-09", 96, 100)
    quota.evaluate("JSearch", "Requests", "2026-09", 96, 100)
    assert len(sent) == 2  # 80 and 95, each sent once
    assert db.one("SELECT COUNT(*) AS n FROM quota_notifications")["n"] == 2
    quota.evaluate("JSearch", "Requests", "2026-10", 100, 100)
    assert len(sent) == 5  # New period: 80, 95 and 100
    def fail(*args):
        raise RuntimeError("mail unavailable")
    monkeypatch.setattr(quota, "send_email", fail)
    quota.evaluate("OpenRouter", "API key spend", "2026-09", 100, 100)
    assert db.one("SELECT status FROM quota_notifications WHERE provider='OpenRouter' "
                  "AND threshold=100")["status"] == "failed"
    monkeypatch.setattr(quota, "send_email", lambda *args: "retry-id")
    quota.evaluate("OpenRouter", "API key spend", "2026-09", 100, 100)
    assert db.one("SELECT status FROM quota_notifications WHERE provider='OpenRouter' "
                  "AND threshold=100")["status"] == "sent"
