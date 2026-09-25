from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .ai import extract_profile, propose_resume_edits
from .alerts import parse_recipients
from .config import settings
from .db import all_rows, connect, init_db, one, utcnow, write
from .evidence import store_evidence
from .resume import export_variant, extract_text
from .search import DEFAULT_ROLES, labels, run_search

DEFAULT_KEYWORDS = ["Research and development", "Quality management", "Product development",
                    "Robotics", "Electronics", "Electrical engineering", "Textiles",
                    "Life sciences", "Consulting"]

app = FastAPI(title="Indian Work Engine", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.middleware("http")
async def prefix_local_redirects(request: Request, call_next):
    response = await call_next(request)
    location = response.headers.get("location", "")
    prefix = settings.app_base_path
    if prefix and location.startswith("/") and not location.startswith("//") \
            and location != prefix and not location.startswith(prefix + "/"):
        response.headers["location"] = prefix + location
    return response


@app.on_event("startup")
def startup():
    if not settings.app_password or not settings.app_secret:
        raise RuntimeError("Set APP_PASSWORD and APP_SECRET in a private .env file")
    init_db()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def session_value() -> str:
    expiry = str(int(time.time()) + 7 * 86400)
    digest = hmac.new(settings.app_secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{digest}"


def authenticated(request: Request) -> bool:
    value = request.cookies.get("work_session", "")
    try:
        expiry, digest = value.split(".", 1)
        expected = hmac.new(settings.app_secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        return int(expiry) > time.time() and hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def auth_or_redirect(request: Request):
    if not authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return None


def csrf_token(request: Request) -> str:
    session = request.cookies.get("work_session", "")
    return hmac.new(settings.app_secret.encode(), f"csrf:{session}".encode(), hashlib.sha256).hexdigest()


async def check_post(request: Request) -> None:
    if not authenticated(request):
        raise HTTPException(401, "Sign in first")
    form = await request.form()
    if not hmac.compare_digest(str(form.get("_csrf", "")), csrf_token(request)):
        raise HTTPException(403, "Invalid form token")


def page(request: Request, name: str, **context):
    return templates.TemplateResponse(
        request, name,
        {"csrf": csrf_token(request), "base_path": settings.app_base_path,
         "profile_name": (one("SELECT name FROM profile WHERE id=1") or {})["name"],
         **context},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "", "base_path": settings.app_base_path})


@app.post("/login")
def login(request: Request, password: str = Form("")):
    if not hmac.compare_digest(password, settings.app_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "That password did not work.",
                                    "base_path": settings.app_base_path}, status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("work_session", session_value(), httponly=True,
                        secure=settings.app_base_url.startswith("https://"),
                        samesite="strict", max_age=7 * 86400,
                        path=settings.app_base_path or "/")
    return response


@app.post("/logout")
async def logout(request: Request):
    await check_post(request)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("work_session", path=settings.app_base_path or "/")
    return response


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if response := auth_or_redirect(request):
        return response
    profile = one("SELECT * FROM profile WHERE id=1")
    resume = one("SELECT * FROM resumes ORDER BY id DESC LIMIT 1")
    latest = all_rows("SELECT * FROM search_runs ORDER BY id DESC LIMIT 5")
    return page(request, "home.html", profile=profile, resume=resume, latest=latest,
                default_roles=", ".join(DEFAULT_ROLES),
                role_suggestions=list(dict.fromkeys(labels(profile["role_labels"]) + DEFAULT_ROLES))[:12],
                keyword_suggestions=list(dict.fromkeys(labels(profile["keywords"]) + DEFAULT_KEYWORDS))[:15])


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    if response := auth_or_redirect(request):
        return response
    return page(request, "profile.html", profile=one("SELECT * FROM profile WHERE id=1"),
                resume=one("SELECT * FROM resumes ORDER BY id DESC LIMIT 1"),
                evidence=all_rows("SELECT section,employer,role,statement FROM resume_evidence "
                                  "WHERE resume_id=(SELECT MAX(id) FROM resumes) ORDER BY id"),
                facts=all_rows("SELECT * FROM facts WHERE active=1 ORDER BY id DESC"))


@app.post("/profile")
async def save_profile(request: Request):
    await check_post(request)
    form = await request.form()
    write(
        "UPDATE profile SET name=?,role_labels=?,keywords=?,locations=?,work_types=?,notes=?,"
        "updated_at=? WHERE id=1",
        (str(form.get("name", "")).strip()[:120], str(form.get("role_labels", ""))[:2000],
         str(form.get("keywords", ""))[:2000], str(form.get("locations", ""))[:1000],
         str(form.get("work_types", ""))[:500], str(form.get("notes", ""))[:3000], utcnow()),
    )
    return RedirectResponse("/profile", status_code=303)


@app.post("/resume")
async def upload_resume(request: Request, resume: UploadFile = File(...)):
    await check_post(request)
    filename = Path(resume.filename or "resume").name
    data = await resume.read(5 * 1024 * 1024 + 1)
    try:
        text, style_sample = extract_text(data, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    stored = settings.data_dir / "uploads" / f"{secrets.token_hex(12)}{Path(filename).suffix.lower()}"
    stored.write_bytes(data)
    resume_id = write(
        "INSERT INTO resumes(filename,stored_path,file_type,resume_text,style_sample,uploaded_at) "
        "VALUES(?,?,?,?,?,?)",
        (filename, str(stored), Path(filename).suffix.lower(), text, style_sample, utcnow()),
    )
    store_evidence(resume_id, text)
    if settings.openrouter_api_key:
        try:
            extracted = extract_profile(text)
            current = one("SELECT * FROM profile WHERE id=1")
            write(
                "UPDATE profile SET name=?,role_labels=?,keywords=?,updated_at=? WHERE id=1",
                (current["name"] or extracted.get("name", ""),
                 current["role_labels"] or ", ".join(extracted.get("suggested_roles", [])),
                 current["keywords"] or ", ".join(extracted.get("keywords", [])), utcnow()),
            )
        except Exception:
            pass  # The upload is still valid; model errors are logged.
    return RedirectResponse("/profile", status_code=303)


@app.post("/search")
async def search(request: Request):
    await check_post(request)
    form = await request.form()
    try:
        run_id = await run_in_threadpool(run_search,
            kind="manual", roles=str(form.get("roles", "")),
            keywords=str(form.get("keywords", "")),
            locations=str(form.get("locations", "")),
            work_types=str(form.get("work_types", "")),
            count=int(form.get("count", 20)),
            days_recent=int(form.get("days_recent", 7)),
        )
    except Exception as exc:
        raise HTTPException(503, f"Search could not finish: {str(exc)[:180]}") from exc
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def results(request: Request, run_id: int):
    if response := auth_or_redirect(request):
        return response
    run = one("SELECT * FROM search_runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404)
    jobs = all_rows(
        "SELECT j.*,r.rank,r.why,s.status AS application_status FROM search_results r "
        "JOIN jobs j ON j.id=r.job_id LEFT JOIN job_status s ON s.job_id=j.id "
        "WHERE r.run_id=? ORDER BY r.rank",
        (run_id,),
    )
    return page(request, "results.html", run=run, jobs=jobs)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    if response := auth_or_redirect(request):
        return response
    job = one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    row = one("SELECT questions_json,why,retirement_signal,retirement_evidence FROM search_results "
              "WHERE job_id=? ORDER BY run_id DESC LIMIT 1",
              (job_id,))
    if row:
        with connect() as db:
            for item in json.loads(row["questions_json"])[:3]:
                if not item.get("question") or not item.get("requirement"):
                    continue
                exists = db.execute(
                    "SELECT 1 FROM questions WHERE job_id=? AND requirement=?",
                    (job_id, item["requirement"]),
                ).fetchone()
                if not exists:
                    db.execute(
                        "INSERT INTO questions(job_id,question,requirement,created_at) VALUES(?,?,?,?)",
                        (job_id, item["question"], item["requirement"], utcnow()),
                    )
    questions = all_rows("SELECT * FROM questions WHERE job_id=? ORDER BY id", (job_id,))
    status = one("SELECT status FROM job_status WHERE job_id=?", (job_id,))
    variants = all_rows("SELECT * FROM resume_variants WHERE job_id=? ORDER BY id DESC", (job_id,))
    return page(request, "job.html", job=job, questions=questions,
                why=row["why"] if row else "", status=status["status"] if status else "New",
                retirement_signal=row["retirement_signal"] if row else "unknown",
                retirement_evidence=row["retirement_evidence"] if row else "",
                variants=variants,
                has_resume=one("SELECT id FROM resumes ORDER BY id DESC LIMIT 1") is not None)


@app.get("/out/{job_id}")
def open_application(request: Request, job_id: str):
    if response := auth_or_redirect(request):
        return response
    job = one("SELECT apply_url FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,?,?)",
          (job_id, "application_link_opened", utcnow()))
    return RedirectResponse(job["apply_url"], status_code=303)


@app.post("/jobs/{job_id}/status")
async def update_status(request: Request, job_id: str):
    await check_post(request)
    form = await request.form()
    status = str(form.get("status", ""))
    if status not in {"New", "Saved", "Applied", "Interviewing", "Rejected", "Closed"}:
        raise HTTPException(400, "Choose a listed status")
    if not one("SELECT 1 FROM jobs WHERE id=?", (job_id,)):
        raise HTTPException(404)
    with connect() as db:
        db.execute(
            "INSERT INTO job_status(job_id,status,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",
            (job_id, status, utcnow()),
        )
        db.execute("INSERT INTO job_activity(job_id,action,status,occurred_at) VALUES(?,?,?,?)",
                   (job_id, "status_changed", status, utcnow()))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/questions/{question_id}")
async def answer_question(request: Request, job_id: str, question_id: int):
    await check_post(request)
    form = await request.form()
    answer = str(form.get("answer", ""))
    detail = str(form.get("detail", "")).strip()[:1000]
    question = one("SELECT * FROM questions WHERE id=? AND job_id=?", (question_id, job_id))
    if not question or answer not in {"Yes", "No", "Not sure"}:
        raise HTTPException(400)
    with connect() as db:
        db.execute("UPDATE questions SET answer=?,detail=?,answered_at=? WHERE id=?",
                   (answer, detail, utcnow(), question_id))
        db.execute("DELETE FROM facts WHERE source=?", (f"answer:{question_id}",))
        if answer == "Yes":
            fact = question["requirement"] + (f" — {detail}" if detail else "")
            db.execute(
                "INSERT INTO facts(fact,source,source_job_id,created_at) VALUES(?,?,?,?)",
                (fact, f"answer:{question_id}", job_id, utcnow()),
            )
        db.execute("INSERT INTO job_activity(job_id,action,occurred_at,detail) VALUES(?,?,?,?)",
                   (job_id, "experience_answered", utcnow(), answer))
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/facts/{fact_id}/remove")
async def remove_fact(request: Request, fact_id: int):
    await check_post(request)
    write("UPDATE facts SET active=0 WHERE id=?", (fact_id,))
    return RedirectResponse("/profile", status_code=303)


@app.get("/my-jobs", response_class=HTMLResponse)
def my_jobs(request: Request):
    if response := auth_or_redirect(request):
        return response
    jobs = all_rows(
        "SELECT j.*,s.status,MAX(a.occurred_at) AS last_opened,COUNT(a.id) AS clicks "
        "FROM job_activity a JOIN jobs j ON j.id=a.job_id "
        "LEFT JOIN job_status s ON s.job_id=j.id "
        "WHERE a.action='application_link_opened' GROUP BY j.id ORDER BY last_opened DESC"
    )
    return page(request, "my_jobs.html", jobs=jobs)


@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    if response := auth_or_redirect(request):
        return response
    return page(request, "alerts.html", alerts=all_rows("SELECT * FROM alerts ORDER BY id DESC"),
                profile=one("SELECT * FROM profile WHERE id=1"),
                delivery_ready=all((settings.jsearch_api_key, settings.openrouter_api_key,
                                    settings.resend_api_key, settings.email_from)))


@app.post("/alerts")
async def add_alert(request: Request):
    await check_post(request)
    form = await request.form()
    try:
        email = ", ".join(parse_recipients(str(form.get("email", ""))))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    time_ist = str(form.get("time_ist", "08:00"))
    count = int(form.get("count", 5))
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_ist) or not 1 <= count <= 50:
        raise HTTPException(400, "Check email, time and number of jobs")
    write(
        "INSERT INTO alerts(name,email,role_labels,keywords,locations,work_types,time_ist,count,"
        "days_recent,created_at,last_sent_date_ist) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (str(form.get("name", "Daily jobs"))[:120], email,
         str(form.get("roles", ""))[:2000], str(form.get("keywords", ""))[:2000],
         str(form.get("locations", ""))[:1000], str(form.get("work_types", ""))[:500],
         time_ist, count, int(form.get("days_recent", 7)), utcnow(),
         datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
         if datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M") >= time_ist else None),
    )
    return RedirectResponse("/alerts", status_code=303)


@app.post("/alerts/{alert_id}/toggle")
async def toggle_alert(request: Request, alert_id: int):
    await check_post(request)
    write("UPDATE alerts SET enabled=1-enabled WHERE id=?", (alert_id,))
    return RedirectResponse("/alerts", status_code=303)


@app.get("/alerts/{alert_id}/edit", response_class=HTMLResponse)
def edit_alert_page(request: Request, alert_id: int):
    if response := auth_or_redirect(request):
        return response
    alert = one("SELECT * FROM alerts WHERE id=?", (alert_id,))
    if not alert:
        raise HTTPException(404)
    return page(request, "alert_edit.html", alert=alert)


@app.post("/alerts/{alert_id}/edit")
async def save_alert(request: Request, alert_id: int):
    await check_post(request)
    form = await request.form()
    try:
        email = ", ".join(parse_recipients(str(form.get("email", ""))))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    time_ist = str(form.get("time_ist", "08:00"))
    count = int(form.get("count", 5))
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_ist) or not 1 <= count <= 50:
        raise HTTPException(400, "Check email, time and number of jobs")
    previous = one("SELECT time_ist FROM alerts WHERE id=?", (alert_id,))
    if not previous:
        raise HTTPException(404)
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    last_sent = (today_ist.date().isoformat() if previous["time_ist"] != time_ist
                 and today_ist.strftime("%H:%M") >= time_ist else None)
    write(
        "UPDATE alerts SET name=?,email=?,role_labels=?,keywords=?,locations=?,work_types=?,"
        "time_ist=?,count=?,days_recent=?,last_sent_date_ist=COALESCE(?,last_sent_date_ist) WHERE id=?",
        (str(form.get("name", "Daily jobs"))[:120], email,
         str(form.get("roles", ""))[:2000], str(form.get("keywords", ""))[:2000],
         str(form.get("locations", ""))[:1000], str(form.get("work_types", ""))[:500],
         time_ist, count, int(form.get("days_recent", 7)), last_sent, alert_id),
    )
    return RedirectResponse("/alerts", status_code=303)


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    if response := auth_or_redirect(request):
        return response
    return page(request, "activity.html",
                runs=all_rows("SELECT * FROM search_runs ORDER BY id DESC LIMIT 50"),
                calls=all_rows("SELECT * FROM api_calls ORDER BY id DESC LIMIT 100"),
                results=all_rows("SELECT r.run_id,j.title,j.company,j.apply_url,r.rank "
                                 "FROM search_results r JOIN jobs j ON j.id=r.job_id "
                                 "ORDER BY r.run_id DESC,r.rank LIMIT 100"),
                emails=all_rows("SELECT * FROM email_runs ORDER BY id DESC LIMIT 50"),
                quotas=all_rows("SELECT * FROM quota_checks ORDER BY id DESC LIMIT 50"),
                quota_notifications=all_rows("SELECT * FROM quota_notifications "
                                             "ORDER BY id DESC LIMIT 50"))


@app.post("/jobs/{job_id}/resume/propose")
async def propose_variant(request: Request, job_id: str):
    await check_post(request)
    job = one("SELECT * FROM jobs WHERE id=?", (job_id,))
    resume = one("SELECT * FROM resumes ORDER BY id DESC LIMIT 1")
    if not job or not resume:
        raise HTTPException(404, "Upload a resume first")
    answers = [dict(x) for x in all_rows(
        "SELECT q.requirement,q.answer,q.detail FROM questions q WHERE q.answered_at IS NOT NULL"
    )]
    if not settings.openrouter_api_key:
        raise HTTPException(503, "OpenRouter is not configured yet")
    changes = await run_in_threadpool(propose_resume_edits, resume["resume_text"], dict(job), answers)
    variant_id = write(
        "INSERT INTO resume_variants(job_id,base_resume_id,proposed_json,created_at) VALUES(?,?,?,?)",
        (job_id, resume["id"], json.dumps(changes, ensure_ascii=False), utcnow()),
    )
    return RedirectResponse(f"/resume-variants/{variant_id}", status_code=303)


@app.get("/resume-variants/{variant_id}", response_class=HTMLResponse)
def variant_page(request: Request, variant_id: int):
    if response := auth_or_redirect(request):
        return response
    variant = one("SELECT * FROM resume_variants WHERE id=?", (variant_id,))
    if not variant:
        raise HTTPException(404)
    job = one("SELECT * FROM jobs WHERE id=?", (variant["job_id"],))
    return page(request, "variant.html", variant=variant, job=job,
                changes=list(enumerate(json.loads(variant["proposed_json"]))),
                approved=json.loads(variant["approved_json"]) if variant["approved_json"] else None)


@app.post("/resume-variants/{variant_id}/approve")
async def approve_variant(request: Request, variant_id: int):
    await check_post(request)
    variant = one("SELECT * FROM resume_variants WHERE id=?", (variant_id,))
    if not variant:
        raise HTTPException(404)
    form = await request.form()
    proposed = json.loads(variant["proposed_json"])
    selected = [proposed[i] for i in range(len(proposed)) if f"change_{i}" in form]
    resume = one("SELECT * FROM resumes WHERE id=?", (variant["base_resume_id"],))
    job = one("SELECT * FROM jobs WHERE id=?", (variant["job_id"],))
    profile = one("SELECT name FROM profile WHERE id=1")
    name = profile["name"] or Path(resume["filename"]).stem
    try:
        docx_path, pdf_path = export_variant(
            Path(resume["stored_path"]), resume["resume_text"], selected,
            name, job["company"], variant_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    write(
        "UPDATE resume_variants SET approved_json=?,approved_at=?,docx_path=?,pdf_path=? WHERE id=?",
        (json.dumps(selected, ensure_ascii=False), utcnow(), str(docx_path), str(pdf_path), variant_id),
    )
    return RedirectResponse(f"/resume-variants/{variant_id}", status_code=303)


@app.get("/resume-variants/{variant_id}/{format_name}")
def download_variant(request: Request, variant_id: int, format_name: str):
    if response := auth_or_redirect(request):
        return response
    if format_name not in {"docx", "pdf"}:
        raise HTTPException(404)
    variant = one("SELECT * FROM resume_variants WHERE id=?", (variant_id,))
    if not variant or not variant["approved_at"]:
        raise HTTPException(404)
    path = Path(variant[f"{format_name}_path"])
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name,
                        media_type=("application/pdf" if format_name == "pdf" else
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
