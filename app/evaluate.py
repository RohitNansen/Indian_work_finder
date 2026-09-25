"""Compare the three OpenRouter models on the same human-reviewed job set.

Usage: python -m app.evaluate --input evaluation.json --output comparison.json
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .ai import rank_jobs
from .db import init_db, one

MODELS = ["google/gemini-3.8-flash", "openai/gpt-5.4-mini", "openai/gpt-5.4"]


def ndcg_at_k(ranked_ids: list[str], labels: dict[str, int], k: int = 5) -> float | None:
    if not labels:
        return None
    def dcg(scores):
        return sum((2 ** score - 1) / math.log2(i + 2)
                   for i, score in enumerate(scores[:k]))
    actual = dcg([labels.get(job_id, 0) for job_id in ranked_ids])
    ideal = dcg(sorted(labels.values(), reverse=True))
    return round(actual / ideal, 4) if ideal else None


def compare(dataset: dict) -> dict:
    profile = dataset["profile"]
    jobs = dataset["jobs"]
    labels = dataset.get("human_labels", {})
    report = {"job_count": len(jobs), "models": {}}
    for model in MODELS:
        before = one("SELECT COALESCE(SUM(estimated_cost_usd),0) AS cost FROM api_calls ")["cost"]
        evaluations = rank_jobs(None, profile, jobs, model=model)
        after = one("SELECT COALESCE(SUM(estimated_cost_usd),0) AS cost FROM api_calls ")["cost"]
        ordered = sorted(evaluations.values(), key=lambda item: item["relevance"], reverse=True)
        report["models"][model] = {
            "estimated_api_cost_usd": round(after - before, 5),
            "ndcg_at_5": ndcg_at_k([item["id"] for item in ordered], labels),
            "ranked_jobs": ordered,
        }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    init_db()
    data = json.loads(Path(args.input).read_text())
    report = compare(data)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote comparison for {report['job_count']} jobs to {args.output}")


if __name__ == "__main__":
    main()
