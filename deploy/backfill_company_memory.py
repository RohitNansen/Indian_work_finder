"""Populate company memory from existing app jobs and saved, capped pilot files."""
from __future__ import annotations

import json

from app.company_memory import remember_company, remember_verified_vacancy
from app.config import settings
from app.db import all_rows


def backfill() -> tuple[int, int]:
    jobs = all_rows("SELECT id,source,company,company_url,apply_url,direct_url,direct_status,"
                    "verification_version FROM jobs")
    for job in jobs:
        remember_company(job["company"], job["source"], job["id"], job["company_url"])
        if job["direct_status"] == "verified" and job["verification_version"] == 3 and job["direct_url"]:
            remember_verified_vacancy(job["company"], job["direct_url"], job["company_url"])
    pilot_count = 0
    for path in sorted((settings.data_dir / "pilots").glob("apify-naukri-query-*.json")):
        for row in json.loads(path.read_text()).get("items", []):
            if not isinstance(row, dict):
                continue
            remember_company(str(row.get("companyName") or ""), "Apify Naukri",
                             str(row.get("jobId") or row.get("portalUrl") or ""),
                             row.get("companyWebsite"))
            pilot_count += 1
    return len(jobs), pilot_count


if __name__ == "__main__":
    job_count, pilot_count = backfill()
    print(f"Company memory updated from {job_count} saved jobs and {pilot_count} pilot records.")
