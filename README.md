# Indian Work Engine

A private job finder for one experienced professional in India. It searches live job listings, curates a short list, records application-link clicks, learns from optional experience answers, and prepares reviewed Word/PDF resume copies for individual jobs.

## Current state

The first version is deployed at `https://hithanis.com/job-finder/` and can also run locally. The supplied text-based resume has been read and mapped into private evidence records. JSearch, OpenRouter and Gmail sending are connected. The daily alert is paused while the owner tests the app. Search quality, resume wording and the three-model comparison still need human validation with live listings. A scanned PDF needs OCR or a text-based copy. See the dated [release status and test path](docs/release-status.md).

## Features

- Upload and replace a PDF or DOCX resume; inspect extracted evidence; edit name, roles, keywords, places and work types.
- Search JSearch on demand, deduplicate results, rank by profile and full job description, and show plain-language reasons without visible scores.
- Ask optional job-specific experience questions; confirmed answers feed future searches.
- Separate **Jobs I opened** table, with click times and manually updated application status.
- Review proposed resume wording changes, approve each change, and download a named Word and PDF copy.
- Create, edit, pause and resume email alerts; default five new roles at 08:00 IST.
- Send alerts with Resend or Gmail SMTP. Gmail uses an app password in the private `.env` and needs no DNS changes.
- Private Activity page with search parameters, returned links, provider calls, token counts, cost estimates, quota checks and delivery results.
- Configurable OpenRouter model, spending cap and JSearch request cap.

The app never submits an application on the person's behalf. Opening a link is recorded as a click, not an application.

## Local setup

Use Python 3.11 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with an `APP_PASSWORD` for the candidate, a different private `OWNER_PASSWORD` for the owner, and a random `APP_SECRET`. The owner password opens **API use** and **Activity**; the candidate password cannot open them. Put provider keys there only after rotating any keys shared in chat. `.env` and `data/` are ignored by Git.

The search count is a maximum, not a guaranteed number. Each run makes fresh source requests, checks promising results against the employer's live vacancy, and recommends only vacancies not shown in an earlier run. Earlier roles remain in their original Recent searches. Search requests, possible listings, listings checked, and suitable jobs are separate progress counts. A run can stop at its source-request cap, company-check cap, time budget, monthly spending guard, or when enough new jobs pass. The owner **API use** page shows recorded costs in USD; JSearch request costs are estimates and provider invoices remain authoritative.

The app also checks a small set of public employer boards at Dozee (Lever) and Instawork (Greenhouse). These feeds cost no API credit and supply original posting dates and company application pages. The Greenhouse verifier checks the individual role against Greenhouse's public Job Board API when its page has no structured job data. Board coverage is deliberately small and is extended only after checking actual job relevance.

The owner-only **Companies** page records unique company names observed in JSearch, the saved Apify Naukri pilot, and employer feeds. Website addresses supplied by listings are hints until an individual employer vacancy has passed title, location, company, and description checks. Verified employer career pages and Lever/Greenhouse boards can be reused in later searches. The registry is filled from existing data with `.venv/bin/python -m deploy.backfill_company_memory`; that reads local records and makes no provider requests. Repeated listings are identified by their verified employer vacancy URL, including when two discovery sources assign different IDs. Company names alone cannot prove that two vacancies are duplicates or that a company is hiring.
Each run also searches up to two verified employer sites, rotating toward sites not checked recently. These are OpenRouter web searches within the existing per-run budget. Links found this way still have to pass the individual-vacancy verifier; a company site or search snippet alone is not enough to recommend a job. Unverified company hints are retained for future resolution but are not crawled as if they were trusted employers.

To compare Naukri as a source, sign in to Apify and save its API token on the VM by running `.venv/bin/python deploy/save_apify_token.py` from this repository folder. The prompt hides the token and saves it only in `.env`; never put it in Git or chat. The owner can then run `.venv/bin/python -m deploy.pilot_apify 1` through `4`, one query at a time. Each pilot query is capped at 25 records (the actor's listed price implies about $0.011 per full 25-record run). Pilot records and direct-link checks are kept privately in `data/pilots/`, and estimated actor use is logged on the owner API use page. The Naukri source is not shown to the client until its job titles, dates, and direct employer links pass the comparison.

For free email sending from a personal Gmail address, set `EMAIL_FROM` to that address and `GMAIL_APP_PASSWORD` to a Google app password created with 2-Step Verification. Gmail takes priority if both Gmail and Resend are configured. Keep the app password out of Git and chat. The app records its own sent-recipient count; Gmail does not provide the app with a live remaining-quota API. Quota warning emails need a working sender to be delivered.

On the VM, the owner can set the app password without showing it in the terminal or shell history: run `.venv/bin/python deploy/save_gmail_app_password.py` from the repository folder, paste at the hidden prompt, and press Enter. Then restart `job-finder.service` to load the new value.

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. For an alert schedule check, run `python -m app.alerts` from a timer once per minute. Alerts created after their configured time wait until the next day; overdue alerts are sent after a VM restart on the same day.
Run `python -m app.quota` hourly to check provider limits and send owner alerts when email delivery is configured.

## Model comparison

The initial model is `google/gemini-3.8-flash`. `app.evaluate` compares it with `openai/gpt-5.4-mini` and `openai/gpt-5.4` on exactly the same profile and job set. See [model evaluation](docs/model-evaluation.md). The final model choice depends on results for the actual resume.

## Deployment

See [GCP VM deployment](docs/deployment.md). The app is designed for one Uvicorn worker behind the existing HTTPS Nginx server at `/job-finder/` on the 1 GB Linux VM. SQLite files, uploads and exports live in a private persistent folder. Keep an off-VM backup.

## Development checks

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The implementation backlog, acceptance criteria and test scenarios are in [backlog](docs/backlog.md).

## Privacy

The app strips email addresses and Indian-format phone numbers from resume text before model requests. It stores the original resume and confirmed answers locally; this is personal data, so restrict VM access and backups. Provider keys, passwords and original resume files are never committed to Git.
