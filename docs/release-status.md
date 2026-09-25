# Release status and change record

The 26 September candidate experience update is described in [its task sheet](upgrade-2026-09-26.md). Daily email is still paused. The original audit below records the previous release; the update has 26 passing regression tests and a three-page visual document check. Live deployment checks are recorded after rollout.

Status checked 26 September 2026, Indian Standard Time. The deployed app uses the `main` branch at `https://hithanis.com/job-finder/`. This file records what has been verified; the [backlog](backlog.md) holds the detailed acceptance criteria and test scenarios. Git commits are the exact change history.

## Current state

| Area | State and evidence | Remaining check |
| --- | --- | --- |
| Private app and hosting | HTTPS login and the Home, Profile, Results, Jobs I opened, Alerts and Activity pages returned HTTP 200 on the VM. Existing apps remained active. | Owner to check ease of use on their laptop or phone. |
| Resume and profile | One resume is stored privately, with 38 extracted evidence statements. Upload, replacement and field editing are implemented. | Owner to verify every extracted statement and role label against the real resume. |
| Live job search | JSearch and OpenRouter keys were accepted. A controlled live search saved 10 listings and shortlisted 5. One extra JSearch request timed out; the search still completed. | Review returned links and role quality over several searches. The app cannot guarantee an employer still accepts applications from an indexed link. |
| Matching and questions | Rules, resume evidence and an OpenRouter full-description review run in sequence. No match percentage is shown. Job pages can ask optional experience questions and save confirmed answers for later searches. | Human review of ranking, explanations and question usefulness. The planned Gemini/GPT-5.4 Mini/GPT-5.4 comparison has not been run. |
| Application tracking | Opening an application link and marking a job Applied are separate actions; the Jobs I opened table is implemented. | Owner to test the flow on real listings. |
| Tailored resumes | Review and approval flow can export company-named Word and PDF files. Synthetic document tests passed. | No real-resume variant has been generated or visually reviewed yet. PDF-source formatting needs special attention. |
| Daily email | Two recipients, five jobs and 08:00 IST are saved. Gmail accepted a setup-test message to the sending account. **The alert is paused until the owner resumes it.** | Confirm test message receipt and perform one scheduled alert send after user testing. No scheduled digest has been sent. |
| Quota and activity logs | JSearch and OpenRouter quota checks returned active; the Activity page and hourly timer work. No unsent quota notice is recorded. Gmail local recipient counts are logged. | A real threshold notification has not occurred. Gmail does not expose remaining quota to this app. |
| Backups | A local SQLite backup was integrity-checked and copied off the VM once. Daily VM backup timer is active. | Automate and test recurring off-VM backups, including uploaded resumes. |

## Changes in this release

- Created the one-user job finder, resume evidence mapping, live search, ranking, questions, application tracking, resume exports and private activity log.
- Deployed behind the existing HTTPS Nginx configuration without changing the two other app routes.
- Added daily alert editing, multiple recipients, Gmail sending, owner quota checks and private credential setup.
- Validated one live search and one Gmail setup message; paused the daily alert while the owner tests the app.

## Owner test path

1. Open the app and inspect Profile, especially resume evidence, role labels and search places.
2. Run a small search. Open a listing, read its full description and check the application link on the employer or job board site.
3. Answer or skip a job question; revisit Profile to confirm only truthful answers are saved.
4. Use Jobs I opened and change a status manually. A click alone should never become Applied.
5. Propose a tailored resume for a promising job. Review every edit and inspect both downloaded files before using them.
6. Review Activity for provider calls and errors. When ready, turn the paused alert on from Alerts and check delivery. If it is already after 08:00 IST, the due alert may run shortly after resuming.

## Important limits

The app can reject listings marked inactive or expired by the API, but it cannot prove a live employer application page is open without checking that page. It does not infer that a company accepts or rejects retirees unless the listing states a relevant rule. The current default model has completed a live call, but shortlist quality and resume voice still require the planned human review.
