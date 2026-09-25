# Deploy on the existing 1 GB GCP Linux VM

The code is prepared for a private Git repository, one Python process, SQLite and systemd timers. The exact commands depend on the VM distribution, domain and SSH setup. Do not put API keys in Git or paste replacement keys into chat.

## Access to provide

- VM external IP or DNS name, GCP project and zone, and Linux distribution/version.
- Preferred SSH username. The safest path is GCP OS Login or a dedicated public SSH key added to the VM. Send only a public key or the non-secret connection details. Do not send a VM password or private key in chat.
- Domain name that will point to the VM, or confirmation that a temporary hostname is acceptable.
- After rotation, place provider keys in a private `.env` on the VM yourself, or use an approved secret-management route.
- A verified email sender domain/address for Resend and the recipient email entered in the app.

## Install outline

1. Create a `jobfinder` Linux user and `/opt/indian-work-engine`, owned by that user.
2. Clone the private Git repository into that directory.
3. Install Python 3.12+, create `.venv`, then install `requirements.txt`.
4. Copy `.env.example` to `.env`, set a long app password, random app secret, the HTTPS base URL and rotated provider keys. Restrict `.env` to the app user with mode `0600`.
5. Set `DATA_DIR=/opt/indian-work-engine/data`; create it and restrict access to the app user. Keep `data/` outside Git.
6. Install `deploy/*.service` and `deploy/*.timer` into `/etc/systemd/system/`, adjusting paths and user if needed. Enable `job-finder.service`, `job-finder-alerts.timer` and `job-finder-backup.timer`.
7. Configure a DNS record and Caddy using `deploy/Caddyfile.example` for automatic HTTPS. Leave Uvicorn bound to `127.0.0.1`.
8. Verify `/healthz`, sign in from the other laptop, run a manual search, click a job, create an alert, and inspect Activity.
9. Copy database backups and uploaded resume files to a private location outside this VM; test one restore. The included daily backup is only an on-disk first copy.

On a 1 GB VM, start with one web worker and ReportLab PDF generation. If LibreOffice is available, PDF exports can retain more of the original DOCX layout, but LibreOffice may need extra memory. The app falls back to a simpler PDF layout if it is absent. Inspect a sample export before relying on it for applications.

## Routine maintenance

- `git pull` then install any updated requirements and restart the web service.
- Review `/activity` for provider errors, requests, returned URLs and model spend.
- Review `journalctl -u job-finder.service` and `journalctl -u job-finder-alerts.service` for operational errors. These system logs should not contain provider keys.
- Rotate provider keys if they were exposed, and update only the private `.env`.
