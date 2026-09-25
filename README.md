# Indian Work Engine

A private job finder for one experienced professional in India. It searches live job listings, curates a short list, records application-link clicks, learns from optional experience answers, and prepares reviewed Word/PDF resume copies for individual jobs.

## Current state

The first version is implemented and runs locally. Search quality, resume wording, and the three-model comparison must be validated with the actual resume and live listings before production use. A scanned PDF needs OCR or a text-based copy.

## Features

- Upload and replace a PDF or DOCX resume; edit name, roles, keywords, places and work types.
- Search JSearch on demand, deduplicate results, rank by profile and full job description, and show plain-language reasons without visible scores.
- Ask optional job-specific experience questions; confirmed answers feed future searches.
- Separate **Jobs I opened** table, with click times and manually updated application status.
- Review proposed resume wording changes, approve each change, and download a named Word and PDF copy.
- Create, edit, pause and resume email alerts; default five new roles at 08:00 IST.
- Private Activity page with search parameters, returned links, provider calls, token counts, cost estimates and delivery results.
- Configurable OpenRouter model, spending cap and JSearch request cap.

The app never submits an application on the person's behalf. Opening a link is recorded as a click, not an application.

## Local setup

Use Python 3.12 or newer.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with a long `APP_PASSWORD` and random `APP_SECRET`. Put provider keys there only after rotating any keys shared in chat. `.env` and `data/` are ignored by Git.

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`. For an alert schedule check, run `python -m app.alerts` from a timer once per minute. Alerts created after their configured time wait until the next day; overdue alerts are sent after a VM restart on the same day.

## Model comparison

The initial model is `google/gemini-3.8-flash`. `app.evaluate` compares it with `openai/gpt-5.4-mini` and `openai/gpt-5.4` on exactly the same profile and job set. See [model evaluation](docs/model-evaluation.md). The final model choice depends on results for the actual resume.

## Deployment

See [GCP VM deployment](docs/deployment.md). The app is designed for one Uvicorn worker behind HTTPS on the existing 1 GB Linux VM. SQLite files, uploads and exports live in a private persistent folder. Keep an off-VM backup.

## Development checks

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The implementation backlog, acceptance criteria and test scenarios are in [backlog](docs/backlog.md).

## Privacy

The app strips email addresses and Indian-format phone numbers from resume text before model requests. It stores the original resume and confirmed answers locally; this is personal data, so restrict VM access and backups. Provider keys, passwords and original resume files are never committed to Git.
