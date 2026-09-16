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
    if not text or re.fullmatch(r"(?:19|20)\d{2}", text): return False
    if text.isdigit(): return len(text) >= 8
    letters = len(re.findall(r"[a-zа-яіїє]", text, re.I)); digits = len(re.findall(r"\d", text))
    return len(text) >= 7 and letters >= 2 and digits >= 2


def _explicit_model(mission: ProductMission) -> str | None:
    for key, value in mission.source_data.items():
        if str(key).casefold() in {"model", "mpn", "ean", "gtin", "gtin13"}:
            text = str(value or "").strip()
            if text: return text
    return None


def _explicit_brand(mission: ProductMission) -> str | None:
    for key, value in mission.source_data.items():
        if str(key).casefold() in {"brand", "manufacturer", "vendor"}:
            text = str(value or "").strip()
            if text and _norm(text) not in {"no brand", "nobrand", "без бренда", "без бренду"}: return text
    return None


def _model_match(expected: str | None, offer_text: str) -> bool:
    if not expected: return False
    if _compact(expected) and _compact(expected) in _compact(offer_text): return True
    parts = [t for t in _norm(expected).split() if t]; offer_norm = _norm(offer_text)
    return len(parts) >= 2 and all(re.search(rf"\b{re.escape(part)}\b", offer_norm) for part in parts)


def _brand_match(expected: str | None, offer_text: str) -> bool:
    return True if not expected else bool(_compact(expected) and _compact(expected) in _compact(offer_text))


_TYPE_GROUPS = {"case":{"футляр","кейс","органайзер","чохол","чехол"},"strap":{"ремінець","ремешок","браслет"},"hammer":{"молоток"},"pencils":{"карандаш","карандаши","олівець","олівці"},"headset":{"гарнитура","навушники","наушники"},"speaker":{"колонка","speaker"},"gas":{"баллон","балон"},"power":{"система","станция","станція","енергообеспечения","живлення"}}

def _type_groups(text: str) -> set[str]:
    tokens=_tokens(text); return {g for g,w in _TYPE_GROUPS.items() if tokens&w}

def _type_conflict(source_text: str, offer_text: str) -> str | None:
    a,b=_type_groups(source_text),_type_groups(offer_text)
    return f"product type mismatch: expected {sorted(a)}, got {sorted(b)}" if a and b and a.isdisjoint(b) else None

def _pack_count(text: str) -> int | None:
    norm=_norm(text)
    for pattern in (r"\b(\d{1,3})\s*(?:шт|штук|pcs|pieces)\b",r"\b(?:набор|комплект|упаковка)\s+(?:из\s+)?(\d{1,3})\b"):
        m=re.search(pattern,norm,re.I)
        if m and int(m.group(1))>1: return int(m.group(1))
    return None

def _quantity_conflict(source_text: str, offer_text: str, *, strong_identity: bool=False) -> str | None:
    expected=_pack_count(source_text)
    if not expected: return None
    actual=_pack_count(offer_text)
    if actual is not None and actual != expected: return f"pack quantity mismatch: expected {expected}, got {actual}"
    if actual is None and not strong_identity: return f"pack quantity not confirmed: expected {expected}"
    return None

def _key_measurements(text: str) -> set[tuple[str,str]]:
    norm=_norm(text); aliases={"г":"g","гр":"g","g":"g","кг":"kg","kg":"kg","вт":"w","w":"w","мм":"mm","mm":"mm","мл":"ml","ml":"ml"}; out=set()
    for value,unit in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*(кг|kg|гр|г|g|вт|w|мм|mm|мл|ml)\b",norm,re.I): out.add((value.replace(",","."),aliases[unit.casefold()]))
    return out

def _measurement_conflict(source_text: str, offer_text: str) -> str | None:
    expected,actual=_key_measurements(source_text),_key_measurements(offer_text)
    for value,unit in expected:
        vals={v for v,u in actual if u==unit}
        if vals and value not in vals: return f"numeric spec mismatch: expected {value}{unit}, got {sorted(vals)}"
    return None


def validate_offer(mission: ProductMission, offer: Offer) -> ValidatedOffer:
    source_text=" ".join(str(v) for v in mission.source_data.values()); offer_text=" ".join([offer.title,*[f"{k} {v}" for k,v in offer.attributes.items()]])
    source_tokens,offer_tokens=_tokens(source_text),_tokens(offer_text); overlap=len(source_tokens&offer_tokens)/max(1,len(source_tokens))
    identifiers=extract_identifiers(mission); matched_ids=[i for i in identifiers if _strong_identifier(i) and _compact(i) and _compact(i) in _compact(offer_text)]
    expected_model=_explicit_model(mission); model_match=_model_match(expected_model,offer_text); expected_brand=_explicit_brand(mission); brand_match=_brand_match(expected_brand,offer_text)
    strong_identity=bool(matched_ids or model_match)
    problems=[_type_conflict(source_text,offer_text),_quantity_conflict(source_text,offer_text,strong_identity=strong_identity),_measurement_conflict(source_text,offer_text),None if brand_match else f"brand not confirmed: {expected_brand}"]
    conflicts=[p for p in problems if p]; positive=[]
    if matched_ids: positive.append("strong identifier match: "+", ".join(matched_ids[:4]))
    if model_match: positive.append("explicit model match: "+str(expected_model))
    if expected_brand and brand_match: positive.append("brand match: "+expected_brand)
    if overlap>=.35: positive.append(f"source token overlap={overlap:.2f}")
    if conflicts: score=min(.64,.20+overlap); verdict=Verdict.CONFLICT if overlap>=.18 else Verdict.REJECT
    elif expected_model and not model_match:
        if overlap>=.18: score=min(.64,.20+overlap); verdict=Verdict.CONFLICT; conflicts.append(f"expected model not confirmed: {expected_model}")
        else: score=overlap; verdict=Verdict.REJECT
    elif strong_identity: score=min(1.0,.72+.05*len(matched_ids)+(.05 if model_match else 0)+.18*overlap); verdict=Verdict.PASS
    elif overlap>=.55: score=min(.79,.35+overlap); verdict=Verdict.PASS
    elif overlap>=.18: score=min(.64,.20+overlap); verdict=Verdict.CONFLICT; conflicts.append("insufficient strong identifier evidence")
    else: score=overlap; verdict=Verdict.REJECT
    return ValidatedOffer(offer=offer,verdict=verdict,score=score,positive_evidence=positive,conflicts=conflicts,rejection_reasons=[] if verdict!=Verdict.REJECT else ["low identity evidence"])
