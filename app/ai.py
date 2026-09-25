from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import settings
from .db import one, utcnow, write

MODEL_PRICES = {
    "google/gemini-3.8-flash": (0.75, 3.75),
    "openai/gpt-5.4-mini": (0.75, 4.50),
    "openai/gpt-5.4": (2.50, 15.00),
}


def public_profile_text(text: str) -> str:
    """Remove contact details before sending resume text to the model."""
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email removed]", text)
    text = re.sub(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)", "[phone removed]", text)
    return text[:30000]


def estimated_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = MODEL_PRICES.get(model, (2.50, 15.00))
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def month_spend() -> float:
    row = one(
        "SELECT COALESCE(SUM(estimated_cost_usd),0) AS total FROM api_calls "
        "WHERE started_at >= strftime('%Y-%m-01T00:00:00','now')"
    )
    return float(row["total"] if row else 0)


def ask_json(*, run_id: int | None, operation: str, instructions: str,
             content: dict[str, Any], schema: dict[str, Any],
             model: str | None = None) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        raise RuntimeError("OpenRouter is not configured yet")
    if month_spend() >= settings.monthly_spend_limit_usd:
        raise RuntimeError("Monthly API spending limit reached")
    model = model or settings.openrouter_model
    call_id = write(
        "INSERT INTO api_calls(run_id,provider,operation,request_json,started_at) VALUES(?,?,?,?,?)",
        (run_id, "OpenRouter", operation,
         json.dumps({"model": model, "content_keys": list(content)}, ensure_ascii=False), utcnow()),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": operation.replace("-", "_"), "strict": True, "schema": schema},
        },
        "provider": {"require_parameters": True},
        "temperature": 0,
    }
    try:
        with httpx.Client(timeout=90) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        message = result["choices"][0]["message"]["content"]
        parsed = json.loads(message)
        usage = result.get("usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or 0)
        out_tokens = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        cost = float(cost) if cost is not None else estimated_cost(model, in_tokens, out_tokens)
        write(
            "UPDATE api_calls SET http_status=?,input_tokens=?,output_tokens=?,"
            "estimated_cost_usd=?,completed_at=? WHERE id=?",
            (response.status_code, in_tokens, out_tokens, cost, utcnow(), call_id),
        )
        return parsed
    except Exception as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        write(
            "UPDATE api_calls SET http_status=?,error=?,completed_at=? WHERE id=?",
            (status, str(exc)[:500], utcnow(), call_id),
        )
        raise


PROFILE_SCHEMA = {
    "type": "object", "properties": {
        "name": {"type": "string"},
        "suggested_roles": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": {"type": "string"}},
    }, "required": ["name", "suggested_roles", "keywords", "facts"],
    "additionalProperties": False,
}


def extract_profile(resume_text: str) -> dict[str, Any]:
    return ask_json(
        run_id=None, operation="resume_profile",
        instructions=(
            "Extract only facts explicitly supported by this resume. Suggest 6-12 senior role "
            "titles and 8-20 search terms spanning transferable R&D, quality, technical leadership, "
            "consulting and relevant industries. Never invent a credential or experience. "
            "Keep the person's own wording where possible."
        ),
        content={"resume": public_profile_text(resume_text)}, schema=PROFILE_SCHEMA,
    )


RANK_SCHEMA = {
    "type": "object", "properties": {
        "jobs": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "relevance": {"type": "integer"},
            "why": {"type": "string"},
            "retirement_signal": {"type": "string", "enum": ["welcomes_retired", "explicit_restriction", "unknown"]},
            "retirement_evidence": {"type": "string"},
            "unconfirmed": {"type": "array", "items": {"type": "object", "properties": {
                "requirement": {"type": "string"},
                "question": {"type": "string"},
            }, "required": ["requirement", "question"], "additionalProperties": False}},
        }, "required": ["id", "relevance", "why", "retirement_signal", "retirement_evidence", "unconfirmed"],
        "additionalProperties": False}},
    }, "required": ["jobs"], "additionalProperties": False,
}


def rank_jobs(run_id: int | None, profile: str, jobs: list[dict[str, Any]],
              model: str | None = None) -> dict[str, dict]:
    """Batch candidate jobs; internal relevance never appears in the user UI."""
    ranked: dict[str, dict] = {}
    for start in range(0, len(jobs), 5):
        batch = jobs[start:start + 5]
        data = ask_json(
            run_id=run_id, operation="rank_jobs",
            instructions=(
                "You are curating jobs for a senior professional with 30+ years of experience. "
                "Evaluate transferable responsibilities and achievements, not mere keyword overlap. "
                "The resume and confirmed answers have priority over unverified claims. "
                "Consider seniority, location, employment type and whether the role is plausibly "
                "open to an experienced retiree. Do not assume age restrictions where unstated. "
                "For retirement_signal, use 'welcomes_retired' only if the listing explicitly "
                "welcomes retired or post-retirement candidates; use 'explicit_restriction' only "
                "if it explicitly states a relevant exclusion or age limit. Otherwise use 'unknown'. "
                "retirement_evidence must be a short exact quote from the job description, "
                "or empty when unknown. Full-time status alone proves neither case. "
                "Return an internal 0-100 relevance number. Write one concise, evidence-grounded "
                "positive reason. For important job requirements not established by the profile, "
                "provide at most three plain questions only when an answer could materially "
                "help this application or support an accurate resume edit. Ask about experience "
                "rather than implying the person lacks a skill. No invented facts."
            ),
            content={"profile": public_profile_text(profile), "jobs": [
                {"id": j["id"], "title": j["title"], "company": j["company"],
                 "location": j["location"], "work_type": j["work_type"],
                 "description": j["description"][:11000]} for j in batch
            ]}, schema=RANK_SCHEMA, model=model,
        )
        for item in data.get("jobs", []):
            if item.get("id") in {j["id"] for j in batch}:
                ranked[item["id"]] = item
    return ranked


EDIT_SCHEMA = {
    "type": "object", "properties": {
        "changes": {"type": "array", "items": {"type": "object", "properties": {
            "original": {"type": "string"},
            "replacement": {"type": "string"},
            "reason": {"type": "string"},
        }, "required": ["original", "replacement", "reason"],
        "additionalProperties": False}},
    }, "required": ["changes"], "additionalProperties": False,
}


def propose_resume_edits(resume_text: str, job: dict, answers: list[dict]) -> list[dict]:
    data = ask_json(
        run_id=None, operation="resume_edits",
        instructions=(
            "Propose at most five targeted replacements to the existing resume for this job. "
            "Use the resume's existing voice, tense, sentence length and terminology. Avoid AI-sounding "
            "language, inflated adjectives and generic claims. The 'original' must be an exact "
            "substring from the resume. Replace only with facts supported by the resume or the "
            "person's confirmed answers. Do not add an unanswered requirement, invent a metric, "
            "change employment dates, or claim a skill merely because the job asks for it. "
            "An empty changes array is correct when no sound edit is needed."
        ),
        content={"resume": public_profile_text(resume_text),
                 "job_title": job["title"], "company": job["company"],
                 "job_description": job["description"][:14000], "confirmed_answers": answers},
        schema=EDIT_SCHEMA,
    )
    # The model must point to real source wording. The person still approves every change.
    return [change for change in data.get("changes", [])
            if change.get("original") and change["original"] in resume_text
            and change.get("replacement")]
