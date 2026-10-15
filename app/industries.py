"""Soft industry preferences: guide discovery and order suitable jobs."""
from __future__ import annotations

from collections import deque

from .matching import normalize


DEFAULT_INDUSTRIES = (
    "Medical devices", "Healthcare technology", "Life sciences",
    "Pharmaceuticals", "Textile machinery",
)

MEDICAL_TERMS = (
    "medical device", "med device", "medical design", "medical product",
    "medical equipment", "medical technology", "biomedical", "medtech", "healthtech",
    "healthcare technology",
    "health care technology", "healthcare product", "life science", "pharmaceutical",
    "pharma", "biotechnology", "biotech", "diagnostic", "surgical device",
    "patient monitoring", "clinical device",
)
TEXTILE_TERMS = (
    "textile", "spinning machine", "yarn", "fabric manufacturing",
    "garment machinery",
)


def family(label: str) -> str:
    value = normalize(label)
    if any(term in value for term in ("medical", "healthcare", "health care", "life science",
                                       "pharma", "biotech", "diagnostic", "clinical")):
        return "medical"
    if any(term in value for term in ("textile", "spinning", "yarn", "fabric")):
        return "textile"
    return value


def families(preferences: list[str]) -> list[str]:
    return list(dict.fromkeys(family(value) for value in preferences if normalize(value)))


def industry_priority(job: dict, preferences: list[str]) -> int:
    """Lower is preferred; unrelated jobs remain eligible after profile checks."""
    ordered = families(preferences)
    if not ordered:
        return 0
    title_company = normalize(f"{job.get('title', '')} {job.get('company', '')}")
    description = normalize(str(job.get("description", ""))[:12000])
    for index, group in enumerate(ordered):
        terms = (MEDICAL_TERMS if group == "medical" else
                 TEXTILE_TERMS if group == "textile" else (group,))
        if group == "medical" and any(term in title_company for term in
                                       ("medical", "healthcare", "health care", "clinical")):
            return index
        if any(term in title_company or term in description for term in terms):
            return index
    return len(ordered)


def preferred_queries(preferences: list[str], locations: list[str]) -> deque[tuple[str, bool, None]]:
    """Try profile-relevant medical and textile roles before general titles."""
    probes = {
        "medical": (
            "medical device R&D leader", "medical device quality head",
            "medical electronics hardware lead", "medtech product development director",
            "life sciences engineering quality head", "pharmaceutical equipment automation lead",
        ),
        "textile": ("textile machinery R&D head", "textile manufacturing quality head"),
    }
    queue: deque[tuple[str, bool, None]] = deque()
    places = locations or ["Chennai"]
    for group in families(preferences)[:3]:
        terms = probes.get(group, (f"{group} engineering R&D lead",))
        for index, term in enumerate(terms):
            place = places[index % len(places)]
            remote = "remote" in place.lower()
            query = f"{term} jobs in {'India remote' if remote else place}"
            queue.append((query, remote, None))
    return queue
