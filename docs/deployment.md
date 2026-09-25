# Deployment on the existing GCP VM

The VM is Debian 12 at `34.66.222.21`. Its home directory contains root-owned `nginx/` and `venv/` folders used by two existing apps. This app lives in its own sibling folder, `/home/rsa-key-20240330/indian-work-engine`. The existing Docker Nginx listens on 80 and 443 and serves `hithanis.com`. The job finder is served at `https://hithanis.com/job-finder/` through a new location in the existing HTTPS server block. No new public listener or DNS record is needed.

## Current resource check

At the initial audit the root filesystem was 20 GB with 6.7 GB free, and the VM had 969 MiB RAM with about 177 MiB available and no swap. The initial app dependencies installed without increasing the disk. Check `df -h /` and `free -h` again before any future expansion. Do not delete files from `nginx/`, its containers, or the existing `venv/` to free space. A disk resize is unnecessary for this first version.
After starting the app, available memory was about 138 MiB. Watch memory during the first live searches; RAM is a closer constraint than disk space.

## Files and Git

The Git repository is public and has `main`, `dev`, and `feature/vm-deployment` branches. The VM clone tracks all three and runs `main`. Never commit `.env`, `data/`, the resume, or generated copies. The ignored `data/` folder holds the SQLite database and resume uploads.

## App service

The app runs as the existing `rsa-key-20240330` account from its own `.venv`. Its service binds to the Docker `webnet` gateway `172.20.0.1:8765`, which is reachable by the Nginx container but is not a new public port. The systemd unit files in `deploy/` use these VM paths. After copying them to `/etc/systemd/system/`, run `systemctl daemon-reload`, enable `job-finder.service`, `job-finder-alerts.timer`, `job-finder-quota.timer`, and `job-finder-backup.timer`, then inspect their status. The timer services use a lock so overlapping checks do not run.

The private `.env` needs `APP_PASSWORD`, `APP_SECRET`, `APP_BASE_URL=https://hithanis.com/job-finder`, `DATA_DIR=./data`, the rotated JSearch and OpenRouter keys, and email settings once a sender is ready. Restrict `.env` to mode `0600`. Set `QUOTA_ALERT_EMAIL` to the owner's address. The recipient for daily job emails remains editable in the app.
An app password and secret were generated on the VM. The owner can view the password in their PuTTY session with `grep '^APP_PASSWORD=' ~/indian-work-engine/.env`; do not paste it into chat or Git. Provider keys remain blank until rotated replacements are entered directly on the VM.

## Proxy change

Insert `deploy/nginx-job-finder-location.conf` into the existing `hithanis.com` HTTPS server block in `~/nginx/default.conf`. Preserve the existing `/vm-pos/`, `/vm-dashboard/`, root redirect, certificate, and port 80 sections exactly. Back up the original file first, test the candidate config with `nginx -t` inside the existing container, and reload Nginx only if validation succeeds. Verify all three routes after reload. The path prefix is part of `APP_BASE_URL`; the app handles prefixed links, redirects, and cookies.
The original config was saved as `~/nginx/default.conf.before-job-finder-20260925`. The reload also activated a renewed certificate that was already on disk; the two existing routes and the new health route returned successfully over certificate-validated HTTPS.

## Checks and limits

Verify the private `/healthz` endpoint from the Nginx container, the public `/job-finder/healthz` endpoint, login, resume upload, search, click tracking, alert editing, and the Activity page. Live searches and email delivery require rotated provider keys and a verified Resend sender. The code records quota checks and tries to email the owner at 80%, 95%, and 100% of readable limits; delivery failures remain visible in Activity. An uncapped OpenRouter key does not expose its remaining account credits through the key endpoint, so set a cap on that key or rely on local spend and provider-error checks.

The daily SQLite backup stays on the same VM. It does not protect against VM or disk loss, and uploads need separate off-VM backup. Copy both privately to another location and test a restore before treating the app as fully backed up.
