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

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .ai import extract_profile, propose_resume_edits
from .auth import session_value, session_role, password_hash, check_password, login_allowed, record_failure
from .alerts import email_ready, parse_recipients
from .config import settings
from .db import all_rows, connect, init_db, one, utcnow, write
from .evidence import store_evidence
from .resume import export_variant, extract_text
from .search import DEFAULT_ROLES, labels, run_search

DEFAULT_KEYWORDS = ["Research and development", "Quality management", "Product development",
                    "Robotics", "Electronics", "Electrical engineering", "Textiles",
                    "Life sciences", "Consulting"]

app = FastAPI(title="Indian Work Engine", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
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
    write("UPDATE search_runs SET status='failed',completed_at=?,error='The app restarted during this search. Please try again.' WHERE kind='manual' AND status='running'", (utcnow(),))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def authenticated(request: Request) -> bool:
    return session_role(request) is not None


def require_admin(request):
    if session_role(request) != "admin":
        raise HTTPException(403, "This page is available to the owner only")


def ist(value):
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
        return stamp.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    except ValueError:
        return str(value)


templates.env.filters["ist"] = ist


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
         "profile_name": (one("SELECT name FROM profile WHERE id=1") or {"name":""})["name"],
         "is_admin": session_role(request) == "admin",
         **context},
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "", "base_path": settings.app_base_path})


@app.post("/login")
def login(request: Request, password: str = Form(""), username: str = Form("")):
    ip = request.client.host if request.client else "unknown"
    role = None
    allowed = login_allowed(ip)
    if allowed and username.strip().casefold() in {"", "admin"} and hmac.compare_digest(password, settings.app_password):
        role = "admin"
    account = one("SELECT password_hash FROM candidate_account WHERE id=1")
    profile = one("SELECT name FROM profile WHERE id=1")
    if allowed and not role and account and username.strip().casefold() == profile["name"].strip().casefold() and check_password(password, account["password_hash"]):
        role = "candidate"
    if not role:
        record_failure(ip)
        return templates.TemplateResponse(request, "login.html", {
            "error": "Please wait 15 minutes before trying again." if not allowed else "Check your name and password.",
            "base_path": settings.app_base_path}, status_code=429 if not allowed else 401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("work_session", session_value(role), httponly=True,
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
    latest = all_rows("SELECT r.*, (SELECT COUNT(DISTINCT sr.job_id) FROM search_results sr JOIN job_activity a ON a.job_id=sr.job_id WHERE sr.run_id=r.id AND a.action IN ('listing_opened','application_link_opened')) AS opened_count, (SELECT COUNT(*) FROM search_results sr JOIN job_status st ON st.job_id=sr.job_id WHERE sr.run_id=r.id AND st.status IN ('Applied','Interviewing','Offer','Rejected')) AS applied_count FROM search_runs r ORDER BY id DESC LIMIT 5")
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
                facts=all_rows("SELECT * FROM facts WHERE active=1 ORDER BY id DESC"),
                candidate_ready=bool(one("SELECT 1 FROM candidate_account WHERE id=1")))


@app.post("/profile")
async def save_profile(request: Request):
    await check_post(request)
    form = await request.form()
    if not str(form.get("name", "")).strip():
        raise HTTPException(400,"Please enter your name")
    write("UPDATE profile SET name=?,notes=?,updated_at=? WHERE id=1",
          (str(form.get("name", "")).strip()[:120], str(form.get("notes", ""))[:3000], utcnow()))

    return RedirectResponse("/profile", status_code=303)


@app.post("/resume")
async def upload_resume(request: Request, resume: UploadFile = File(...)):
    await check_post(request)
    filename = Path(resume.filename or "resume").name
    data = await resume.read(5 * 1024 * 1024 + 1)
    try:
        text, style_sample = await run_in_threadpool(extract_text, data, filename)
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
            extracted = await run_in_threadpool(extract_profile, text)
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
async def search(request: Request, background: BackgroundTasks):
    await check_post(request)
    form = await request.form()
    if not settings.jsearch_api_key:
        raise HTTPException(503, "Job search is not configured yet. Please contact the owner.")
    try:
        count=max(1,min(100,int(form.get("count",20))))
        days=int(form.get("days_recent",7))
        if days not in {1,3,7,30}:raise ValueError()
    except ValueError:
        raise HTTPException(400,"Choose a listed job count and date range")
    args=dict(kind="manual",roles=str(form.get("roles",""))[:2000],
              keywords=str(form.get("keywords",""))[:2000],locations=str(form.get("locations",""))[:1000],
              work_types=str(form.get("work_types",""))[:500],count=count,days_recent=days)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        running=db.execute("SELECT id FROM search_runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
        if running:
            return RedirectResponse(f"/runs/{running['id']}",status_code=303)
        db.execute("UPDATE profile SET role_labels=?,keywords=?,locations=?,work_types=?,updated_at=? WHERE id=1",
                   (args['roles'],args['keywords'],args['locations'],args['work_types'],utcnow()))
        run_id=db.execute("INSERT INTO search_runs(kind,started_at,parameters_json) VALUES('manual',?,?)",
                          (utcnow(),json.dumps(args))).lastrowid
    def finish_search():
        try:
            run_search(**args,existing_run_id=run_id)
        except Exception:
            pass  # Search status and diagnostic are persisted by run_search.
    background.add_task(finish_search)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def results(request: Request, run_id: int):
    if response := auth_or_redirect(request):
        return response
    run = one("SELECT * FROM search_runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404)
    jobs = all_rows(
        "SELECT j.*,r.rank,r.why,COALESCE(NULLIF(s.status,'New'),CASE WHEN EXISTS(SELECT 1 FROM job_activity a WHERE a.job_id=j.id AND a.action IN ('listing_opened','application_link_opened')) THEN 'Opened' ELSE 'Unopened' END) AS application_status FROM search_results r "
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
    from .questions import ensure_questions
    ensure_questions(dict(job))
    write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,?,?)", (job_id,"listing_opened",utcnow()))
    questions = all_rows("SELECT * FROM questions WHERE job_id=? AND answered_at IS NULL ORDER BY id LIMIT 3", (job_id,))
    answered = all_rows("SELECT * FROM questions WHERE job_id=? AND answered_at IS NOT NULL ORDER BY answered_at DESC", (job_id,))
    status = one("SELECT status FROM job_status WHERE job_id=?", (job_id,))
    variants = all_rows("SELECT * FROM resume_variants WHERE job_id=? ORDER BY id DESC", (job_id,))
    return page(request, "job.html", job=job, questions=questions, answered=answered,
                why=row["why"] if row else "", status=status["status"] if status and status["status"] != "New" else "Opened",
                retirement_signal=row["retirement_signal"] if row else "unknown",
                retirement_evidence=row["retirement_evidence"] if row else "",
                variants=variants,
                has_resume=one("SELECT id FROM resumes ORDER BY id DESC LIMIT 1") is not None)


@app.get("/out/{job_id}")
def open_application(request: Request, job_id: str):
    if response := auth_or_redirect(request):
        return response
    job = one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    from .direct_links import resolve_job
    resolved = resolve_job(dict(job), force=True)
    if not resolved.get("direct_url") or resolved.get("direct_status") != "verified":
        return RedirectResponse(f"/jobs/{job_id}?link_unavailable=1", status_code=303)
    write("INSERT INTO job_activity(job_id,action,occurred_at) VALUES(?,?,?)",
          (job_id, "application_link_opened", utcnow()))
    return RedirectResponse(resolved["direct_url"], status_code=303)


@app.post("/jobs/{job_id}/status")
async def update_status(request: Request, job_id: str):
    await check_post(request)
    form = await request.form()
    status = str(form.get("status", ""))
    if status not in {"Unopened", "Opened", "Saved", "Applied", "Interviewing", "Offer", "Rejected", "Closed"}:
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
    return RedirectResponse(f"/jobs/{job_id}?saved=1#experience", status_code=303)


@app.post("/facts/{fact_id}/remove")
async def remove_fact(request: Request, fact_id: int):
    await check_post(request)
    fact = one("SELECT source FROM facts WHERE id=?", (fact_id,))
    write("UPDATE facts SET active=0 WHERE id=?", (fact_id,))
    if fact and fact["source"].startswith("answer:"):
        write("UPDATE questions SET answer='Removed',detail='' WHERE id=?", (int(fact["source"].split(":")[1]),))
    return RedirectResponse("/profile", status_code=303)


@app.get("/my-jobs", response_class=HTMLResponse)
def my_jobs(request: Request):
    if response := auth_or_redirect(request):
        return response
    jobs = all_rows(
        "SELECT j.*,s.status,MAX(a.occurred_at) AS last_opened,COUNT(a.id) AS clicks "
        "FROM job_activity a JOIN jobs j ON j.id=a.job_id "
        "LEFT JOIN job_status s ON s.job_id=j.id "
        "WHERE a.action IN ('listing_opened','application_link_opened') GROUP BY j.id ORDER BY last_opened DESC"
    )
    return page(request, "my_jobs.html", jobs=jobs)


@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    if response := auth_or_redirect(request):
        return response
    return page(request, "alerts.html", alerts=all_rows("SELECT * FROM alerts ORDER BY id DESC"),
                profile=one("SELECT * FROM profile WHERE id=1"),
                delivery_ready=bool(settings.jsearch_api_key and settings.openrouter_api_key
                                    and email_ready()))


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
    require_admin(request)
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
    answers = [dict(x) for x in all_rows("SELECT fact FROM facts WHERE active=1")]
    if not settings.openrouter_api_key:
        raise HTTPException(503, "OpenRouter is not configured yet")
    try:
        changes = await run_in_threadpool(propose_resume_edits, resume["resume_text"], dict(job), answers)
    except Exception as exc:
        raise HTTPException(503, "The resume suggestions could not be prepared. Please try again later; your uploaded file is safe.") from exc
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
                approved=json.loads(variant["approved_json"]) if variant["approved_json"] and variant["layout_version"]==2 else None)


@app.post("/resume-variants/{variant_id}/approve")
async def approve_variant(request: Request, variant_id: int):
    await check_post(request)
    variant = one("SELECT * FROM resume_variants WHERE id=?", (variant_id,))
    if not variant:
        raise HTTPException(404)
    form = await request.form()
    proposed = json.loads(variant["proposed_json"])
    selected = [dict(proposed[i],replacement=str(form.get(f"replacement_{i}",proposed[i]["replacement"])).strip()[:1500]) for i in range(len(proposed)) if f"change_{i}" in form]
    if any(not x['replacement'] for x in selected):
        raise HTTPException(400,"Enter the replacement wording or deselect the edit")
    resume = one("SELECT * FROM resumes WHERE id=?", (variant["base_resume_id"],))
    job = one("SELECT * FROM jobs WHERE id=?", (variant["job_id"],))
    profile = one("SELECT name FROM profile WHERE id=1")
    name = profile["name"] or Path(resume["filename"]).stem
    try:
        docx_path, pdf_path = await run_in_threadpool(export_variant,
            Path(resume["stored_path"]), resume["resume_text"], selected,
            name, job["company"], variant_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    write(
        "UPDATE resume_variants SET approved_json=?,approved_at=?,docx_path=?,pdf_path=?,layout_version=2 WHERE id=?",
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
    if variant["layout_version"] != 2:
        return RedirectResponse(f"/resume-variants/{variant_id}",status_code=303)
    path = Path(variant[f"{format_name}_path"])
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name,
                        media_type=("application/pdf" if format_name == "pdf" else
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))


@app.post("/candidate-account")
async def save_candidate_account(request: Request):
    await check_post(request)
    require_admin(request)
    form = await request.form()
    password = str(form.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9]{6}", password):
        raise HTTPException(400, "Choose exactly six letters or numbers")
    write("INSERT INTO candidate_account(id,password_hash,updated_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,updated_at=excluded.updated_at",
          (password_hash(password),utcnow()))
    return RedirectResponse("/profile?account_saved=1#account", status_code=303)


@app.post("/facts/{fact_id}/edit")
async def edit_fact(request: Request, fact_id: int):
    await check_post(request)
    form = await request.form()
    value = str(form.get("fact", "")).strip()[:1500]
    fact = one("SELECT * FROM facts WHERE id=? AND active=1", (fact_id,))
    if not value or not fact:
        raise HTTPException(400, "Enter the experience you want to save")
    write("UPDATE facts SET fact=? WHERE id=?", (value,fact_id))
    if fact["source"].startswith("answer:"):
        write("UPDATE questions SET detail=?,answered_at=? WHERE id=?", (value,utcnow(),int(fact["source"].split(":")[1])))
    return RedirectResponse("/profile?experience_saved=1#memory", status_code=303)


@app.post("/jobs/{job_id}/verify")
async def verify_company_link(request: Request, job_id: str):
    await check_post(request)
    from .direct_links import resolve_job, find_employer_on_web
    from .search import fetch_page
    job = one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(404)
    def discover(candidate):
        if not settings.jsearch_api_key:
            return []
        try:
            rows, _ = fetch_page(None, f'{candidate["title"]} {candidate["company"]} careers India', False, None, 30)
            return rows
        except Exception:
            return []
    await run_in_threadpool(resolve_job, dict(job), force=True, discover=discover, web_discover=find_employer_on_web)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.exception_handler(HTTPException)
async def friendly_error(request: Request, exc: HTTPException):
    if not authenticated(request):
        return templates.TemplateResponse(request,"login.html",{"error":str(exc.detail),"base_path":settings.app_base_path},status_code=exc.status_code)
    return templates.TemplateResponse(request,"error.html",{
        "message":str(exc.detail),"csrf":csrf_token(request),"base_path":settings.app_base_path,
        "is_admin":session_role(request)=="admin"},status_code=exc.status_code)
