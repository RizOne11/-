from __future__ import annotations

import re
from typing import Any

from puma_scouts.models import Offer, ProductMission, ValidatedOffer, Verdict
from puma_scouts.query import extract_identifiers


def _norm(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {t for t in _norm(value).split() if len(t) >= 3}


def _strong_identifier(value: str) -> bool:
    text = _norm(value).replace(" ", "")
    if not text or re.fullmatch(r"(?:19|20)\d{2}", text):
        return False
    if text.isdigit():
        return len(text) >= 8
    # Model/MPN-like identifiers should carry both letters and digits.
    return len(text) >= 4 and bool(re.search(r"[a-zа-яіїє]", text, re.I)) and bool(re.search(r"\d", text))


def validate_offer(mission: ProductMission, offer: Offer) -> ValidatedOffer:
    source_text = " ".join(str(v) for v in mission.source_data.values())
    offer_text = " ".join([offer.title, *[f"{k} {v}" for k, v in offer.attributes.items()]])
    source_tokens = _tokens(source_text)
    offer_tokens = _tokens(offer_text)
    overlap = len(source_tokens & offer_tokens) / max(1, len(source_tokens))

    identifiers = extract_identifiers(mission)
    matched_ids = [i for i in identifiers if _strong_identifier(i) and _norm(i) and _norm(i) in _norm(offer_text)]
    positive: list[str] = []
    conflicts: list[str] = []

    if matched_ids:
        positive.append("strong identifier match: " + ", ".join(matched_ids[:4]))
    if overlap >= 0.35:
        positive.append(f"source token overlap={overlap:.2f}")

    if matched_ids:
        score = min(1.0, 0.72 + 0.05 * len(matched_ids) + 0.18 * overlap)
        verdict = Verdict.PASS
    elif overlap >= 0.55:
        score = min(0.79, 0.35 + overlap)
        verdict = Verdict.PASS
    elif overlap >= 0.18:
        score = min(0.64, 0.20 + overlap)
        verdict = Verdict.CONFLICT
        conflicts.append("insufficient strong identifier evidence")
    else:
        score = overlap
        verdict = Verdict.REJECT

    return ValidatedOffer(
        offer=offer,
        verdict=verdict,
        score=score,
        positive_evidence=positive,
        conflicts=conflicts,
        rejection_reasons=[] if verdict != Verdict.REJECT else ["low identity evidence"],
    )
