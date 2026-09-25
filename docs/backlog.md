# Product backlog and task sheets

The first release serves one experienced professional. It prioritizes usable job suggestions and clear controls over a large dashboard.

## PBI 1 — Profile and replaceable resume

**Description:** Upload PDF/DOCX, extract text, suggest roles/keywords, and allow manual corrections. Keep previous uploads for provenance while using the newest for recommendations.

**Acceptance criteria:** A text-based file under 5 MB uploads; the user sees its name; a later upload becomes current; field edits persist; unsupported scanned files show a clear message; resume files are outside Git.

**Test scenarios:** Upload valid DOCX/PDF, replace it, try an invalid file and oversized file, edit role/location fields, restart app and confirm persistence.

**Status:** Implemented; real-resume validation pending.

## PBI 2 — Broad job search and curated shortlist

**Description:** Search related roles across Chennai, Tamil Nadu, Bengaluru and eligible remote India jobs; page through JSearch within a configurable request cap. Use rules plus full-description LLM review.

**Acceptance criteria:** User can type or edit role/keyword labels, set places, work types, recency and requested count; duplicate jobs appear once; results have working application links and reasons; no match percentage is displayed. When fewer credible jobs exist than requested, show the available number.

**Test scenarios:** Duplicate API results, empty results, provider failure, location mismatch, a transferable senior role with a different title, and a junior role with overlapping keywords.

**Status:** Implemented; live India coverage and link checks pending.

## PBI 3 — Job questions and growing experience memory

**Description:** On a job page, ask up to three optional questions only when an answer could help with the application or a truthful resume edit. Store and reuse confirmed experience.

**Acceptance criteria:** Questions appear after opening the job; “No” and “Not sure” are possible; user may skip; an answer can be changed; removed/changed claims no longer influence later searches; application link stays available.

**Test scenarios:** Confirm experience and find it in the profile; change Yes to No and ensure the fact disappears; skip all questions; verify no unsupported fact enters the resume proposal.

**Status:** Implemented; question quality to be tested with actual resume and jobs.

## PBI 4 — Click and application tracking

**Description:** Record application-link opens separately from application status.

**Acceptance criteria:** **Jobs I opened** shows job, company, last click time, click count and status. A click does not automatically mark Applied. Statuses: New, Saved, Applied, Interviewing, Rejected, Closed.

**Test scenarios:** Open twice, view click count, mark Applied, refresh and confirm state.

**Status:** Implemented and locally tested.

## PBI 5 — Reviewed tailored resume files

**Description:** Compare a job description and confirmed experience with the current resume. Propose exact-source wording replacements in the resume's own voice. Let the person approve individual changes and download named DOCX/PDF files.

**Acceptance criteria:** Every suggested replacement points to text already in the resume; user can reject every edit; original file remains untouched; filenames include profile name and company; Word file is editable; PDF is readable; no invented facts or metrics. For DOCX originals, paragraph styles are retained as far as possible.

**Test scenarios:** Approve one of two changes; reject all; use an answer confirmed for this job; refuse a proposal that cannot be located in the original; inspect exported files visually against the real resume.

**Status:** Implemented; real-resume voice and layout review pending. PDF-source uploads create a new editable Word layout, so visual similarity needs review.

## PBI 6 — Scheduled email alerts

**Description:** Create editable alerts with recipient email, criteria, delivery time in IST, recent-post window and maximum jobs. Send only new jobs for each alert.

**Acceptance criteria:** Default is five jobs at 08:00 IST; email/time/count/criteria can be edited; alert can be paused; fewer than five new jobs yields a shorter email; zero yields a clear email; daily sends are recorded.

**Test scenarios:** Due alert in IST, changed delivery time, repeated scheduler invocation, zero/fewer new jobs, email provider failure and retry.

**Status:** Implemented; live email delivery pending sender configuration.

## PBI 7 — Admin activity and cost control

**Description:** Keep a private log in SQLite on the VM and show owner-readable tables.

**Acceptance criteria:** Log runs, exact search parameters, provider calls and errors, returned links, rankings, clicks, email outcomes, token counts and cost estimates. Keep credentials out of logs and Git. Enforce monthly spend and per-search request caps.

**Test scenarios:** Search failure retains an error record; cost totals update; returned URL is visible in Activity; `.env`, uploads and database remain ignored by Git.

**Status:** Implemented; end-to-end live provider audit pending.

## PBI 8 — Deployment and quality gate

**Description:** Publish Git repo, install app on the 1 GB Linux VM, enable HTTPS and systemd timers, set backups and compare three OpenRouter models on a human-reviewed job set.

**Acceptance criteria:** App works from another laptop through HTTPS; secrets exist only on VM; 08:00 IST email sends; fresh DB backup can be restored; selected model meets ranking and factuality checks.

**Test scenarios:** Clean install from Git, restart VM, edit alert then confirm send, restore backup, compare model top-five lists and resume edits, test mobile-width UI.

**Status:** Pending GitHub sign-in, VM connection details, rotated keys, sender setup and actual resume.
