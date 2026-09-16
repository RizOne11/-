from __future__ import annotations

import re
from typing import Any

from puma_scouts.models import Offer, ProductMission, ValidatedOffer, Verdict
from puma_scouts.query import extract_identifiers


def _norm(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^\w]", "", str(value or "").casefold(), flags=re.UNICODE)


def _tokens(value: Any) -> set[str]:
    return {t for t in _norm(value).split() if len(t) >= 3}


def _strong_identifier(value: str) -> bool:
    text = _compact(value)
    if not text or re.fullmatch(r"(?:19|20)\d{2}", text):
        return False
    if text.isdigit():
        return len(text) >= 8
    letters = len(re.findall(r"[a-zа-яіїє]", text, re.I))
    digits = len(re.findall(r"\d", text))
    return len(text) >= 7 and letters >= 2 and digits >= 2


def _explicit_model(mission: ProductMission) -> str | None:
    for key, value in mission.source_data.items():
        if str(key).casefold() in {"model", "mpn", "ean", "gtin", "gtin13"}:
            text = str(value or "").strip()
            if text:
                return text
    return None


def _explicit_brand(mission: ProductMission) -> str | None:
    for key, value in mission.source_data.items():
        if str(key).casefold() in {"brand", "manufacturer", "vendor"}:
            text = str(value or "").strip()
            if text and _norm(text) not in {"no brand", "nobrand", "без бренда", "без бренду"}:
                return text
    return None


def _model_tokens(value: str) -> list[str]:
    return [t for t in _norm(value).split() if t]


def _model_match(expected: str | None, offer_text: str) -> bool:
    if not expected:
        return False
    compact_expected = _compact(expected)
    compact_offer = _compact(offer_text)
    if compact_expected and compact_expected in compact_offer:
        return True
    parts = _model_tokens(expected)
    offer_norm = _norm(offer_text)
    return len(parts) >= 2 and all(re.search(rf"\b{re.escape(part)}\b", offer_norm) for part in parts)


def _brand_match(expected: str | None, offer_text: str) -> bool:
    if not expected:
        return True
    return bool(_compact(expected) and _compact(expected) in _compact(offer_text))


_TYPE_GROUPS = {
    "case": {"футляр", "кейс", "органайзер", "чохол", "чехол"},
    "strap": {"ремінець", "ремешок", "браслет"},
    "hammer": {"молоток"},
    "pencils": {"карандаш", "карандаши", "олівець", "олівці"},
    "headset": {"гарнитура", "навушники", "наушники"},
    "speaker": {"колонка", "speaker"},
    "gas": {"баллон", "балон"},
    "power": {"система", "станция", "станція", "енергообеспечения", "живлення"},
}


def _type_groups(text: str) -> set[str]:
    tokens = _tokens(text)
    return {group for group, words in _TYPE_GROUPS.items() if tokens & words}


def _type_conflict(source_text: str, offer_text: str) -> str | None:
    source_groups = _type_groups(source_text)
    offer_groups = _type_groups(offer_text)
    if source_groups and offer_groups and source_groups.isdisjoint(offer_groups):
        return f"product type mismatch: expected {sorted(source_groups)}, got {sorted(offer_groups)}"
    return None


def _pack_count(text: str) -> int | None:
    norm = _norm(text)
    patterns = (
        r"\b(\d{1,3})\s*(?:шт|штук|pcs|pieces)\b",
        r"\b(?:набор|комплект|упаковка)\s+(?:из\s+)?(\d{1,3})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, norm, re.I)
        if match:
            value = int(match.group(1))
            if value > 1:
                return value
    return None


def _quantity_conflict(source_text: str, offer_text: str) -> str | None:
    expected = _pack_count(source_text)
    if not expected:
        return None
    actual = _pack_count(offer_text)
    if actual != expected:
        return f"pack quantity not confirmed: expected {expected}, got {actual or 1}"
    return None


def _key_measurements(text: str) -> set[tuple[str, str]]:
    norm = _norm(text)
    aliases = {
        "г": "g", "гр": "g", "g": "g",
        "кг": "kg", "kg": "kg",
        "вт": "w", "w": "w",
        "мм": "mm", "mm": "mm",
        "мл": "ml", "ml": "ml",
    }
    result: set[tuple[str, str]] = set()
    for value, unit in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*(кг|kg|гр|г|g|вт|w|мм|mm|мл|ml)\b", norm, re.I):
        result.add((value.replace(",", "."), aliases[unit.casefold()]))
    return result


def _measurement_conflict(source_text: str, offer_text: str) -> str | None:
    expected = _key_measurements(source_text)
    if not expected:
        return None
    actual = _key_measurements(offer_text)
    # Only reject when the candidate states the same kind of measurement but
    # with a different value. Missing specs remain unknown rather than false.
    for value, unit in expected:
        actual_values = {v for v, u in actual if u == unit}
        if actual_values and value not in actual_values:
            return f"numeric spec mismatch: expected {value}{unit}, got {sorted(actual_values)}"
    return None


def validate_offer(mission: ProductMission, offer: Offer) -> ValidatedOffer:
    source_text = " ".join(str(v) for v in mission.source_data.values())
    offer_text = " ".join([offer.title, *[f"{k} {v}" for k, v in offer.attributes.items()]])
    source_tokens = _tokens(source_text)
    offer_tokens = _tokens(offer_text)
    overlap = len(source_tokens & offer_tokens) / max(1, len(source_tokens))

    identifiers = extract_identifiers(mission)
    matched_ids = [i for i in identifiers if _strong_identifier(i) and _compact(i) and _compact(i) in _compact(offer_text)]
    expected_model = _explicit_model(mission)
    model_match = _model_match(expected_model, offer_text)
    expected_brand = _explicit_brand(mission)
    brand_match = _brand_match(expected_brand, offer_text)
    positive: list[str] = []
    conflicts: list[str] = []

    type_problem = _type_conflict(source_text, offer_text)
    quantity_problem = _quantity_conflict(source_text, offer_text)
    measurement_problem = _measurement_conflict(source_text, offer_text)
    brand_problem = None if brand_match else f"brand not confirmed: {expected_brand}"
    for problem in (type_problem, quantity_problem, measurement_problem, brand_problem):
        if problem:
            conflicts.append(problem)

    if matched_ids:
        positive.append("strong identifier match: " + ", ".join(matched_ids[:4]))
    if model_match:
        positive.append("explicit model match: " + str(expected_model))
    if expected_brand and brand_match:
        positive.append("brand match: " + expected_brand)
    if overlap >= 0.35:
        positive.append(f"source token overlap={overlap:.2f}")

    # Explicit identity conflicts override fuzzy similarity.
    if type_problem or quantity_problem or measurement_problem or brand_problem:
        score = min(0.64, 0.20 + overlap)
        verdict = Verdict.CONFLICT if overlap >= 0.18 else Verdict.REJECT
    elif expected_model and not model_match:
        if overlap >= 0.18:
            score = min(0.64, 0.20 + overlap)
            verdict = Verdict.CONFLICT
            conflicts.append(f"expected model not confirmed: {expected_model}")
        else:
            score = overlap
            verdict = Verdict.REJECT
    elif matched_ids or model_match:
        score = min(1.0, 0.72 + 0.05 * len(matched_ids) + (0.05 if model_match else 0) + 0.18 * overlap)
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
