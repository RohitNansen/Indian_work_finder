"""Traceable resume statements and a small, explainable first-pass matcher."""
from __future__ import annotations

import re

from .db import all_rows, connect

HEADINGS = {
    "PROFESSIONAL SUMMARY": "summary",
    "CORE COMPETENCIES": "competency",
    "PROFESSIONAL EXPERIENCE": "experience",
    "EDUCATION & PROFESSIONAL TRAINING": "education",
    "TECHNICAL TOOLS & SYSTEMS": "tools",
}
STOP = {"with", "from", "this", "that", "their", "and", "for", "the", "into", "using",
        "more", "than", "including", "work", "years", "have", "will", "your", "role",
        "jobs", "team", "teams", "project", "projects", "products", "product"}
EQUIVALENTS = {
    "research": "research", "r&d": "research", "development": "development",
    "automation": "automation", "automated": "automation",
    "robotic": "robotics", "robotics": "robotics",
    "electronic": "electronics", "electronics": "electronics",
    "electrical": "electrical", "quality": "quality", "qms": "quality",
    "medical": "medical", "healthcare": "medical", "device": "device", "devices": "device",
    "compliance": "compliance", "regulatory": "compliance",
    "textile": "textile", "textiles": "textile", "spinning": "textile",
    "control": "controls", "controls": "controls",
    "embedded": "embedded", "motion": "motion",
    "consulting": "consulting", "consultant": "consulting", "advisor": "consulting",
    "leadership": "leadership", "leader": "leadership", "lead": "leadership",
    "design": "design", "designed": "design", "engineering": "engineering",
}


def terms(text: str) -> set[str]:
    raw = re.findall(r"[A-Za-z][A-Za-z0-9+#-]{2,}", text.lower())
    return {EQUIVALENTS.get(word, word) for word in raw if word not in STOP}


def parse_evidence(text: str) -> list[dict[str, str]]:
    """Keep original resume wording; never infer an achievement or employer."""
    section = "other"
    employer = role = ""
    experience_heading_next = False
    pending = ""
    items: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            statement = re.sub(r"\s+", " ", pending).strip()
            items.append({"section": section, "employer": employer if section == "experience" else "",
                          "role": role if section == "experience" else "", "statement": statement,
                          "search_terms": ",".join(sorted(terms(statement)))})
            pending = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = next((value for key, value in HEADINGS.items() if line.upper().startswith(key)), None)
        if heading:
            flush()
            section, employer, role = heading, "", ""
            experience_heading_next = section == "experience"
            continue
        if section == "experience" and not line.startswith(("•", "-")):
            if experience_heading_next or (line.endswith(("Ltd.", "Pvt. Ltd.", "Limited")) and len(line) < 100):
                flush()
                employer, role = line, ""
                experience_heading_next = False
                continue
            if employer and not role and "|" in line:
                role = line
                continue
        if line.startswith(("•", "-")):
            flush()
            pending = line.lstrip("•- ")
        elif pending:
            pending += ("" if pending.endswith("-") else " ") + line
    flush()
    return items


def store_evidence(resume_id: int, text: str) -> int:
    items = parse_evidence(text)
    with connect() as db:
        db.execute("DELETE FROM resume_evidence WHERE resume_id=?", (resume_id,))
        db.executemany(
            "INSERT INTO resume_evidence(resume_id,section,employer,role,statement,search_terms) "
            "VALUES(?,?,?,?,?,?)",
            [(resume_id, x["section"], x["employer"], x["role"], x["statement"],
              x["search_terms"]) for x in items],
        )
    return len(items)


def current_evidence():
    return all_rows("SELECT * FROM resume_evidence WHERE resume_id=(SELECT MAX(id) FROM resumes)")


def evidence_matches(job: dict, evidence=None, limit: int = 5) -> list[tuple[float, object]]:
    evidence = evidence if evidence is not None else current_evidence()
    job_terms = terms(" ".join((job["title"], job["description"])))
    title_terms = terms(job["title"])
    ranked = []
    for row in evidence:
        statement_terms = set(row["search_terms"].split(",")) - {""}
        overlap = job_terms & statement_terms
        if not overlap:
            continue
        overlap_in_title = title_terms & statement_terms
        section_weight = 1.25 if row["section"] == "experience" else 1.0
        score = section_weight * (len(overlap) + 2.5 * len(overlap_in_title))
        ranked.append((score, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[:limit]
