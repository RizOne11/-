from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from puma_scouts.models import Offer, ProductMission
from puma_scouts.query import extract_identifiers


class PivotKind(StrEnum):
    IDENTIFIER = "identifier"
    BRAND_MODEL = "brand_model"
    SELLER = "seller"
    MARKETPLACE_CARD = "marketplace_card"
    ALIAS = "alias"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True, slots=True)
class DiscoveryPivot:
    """A newly learned fact that can open a different search graph."""

    kind: PivotKind
    value: str
    source: str
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()


@dataclass(slots=True)
class DiscoveryState:
    """State shared across search rounds.

    The important rule is that a Scout must not merely generate more variants of
    the original query.  Every round should ask whether a newly discovered fact
    opens a *different* route to the same product.
    """

    seen_queries: set[str] = field(default_factory=set)
    seen_pivots: set[tuple[PivotKind, str]] = field(default_factory=set)
    pivots: list[DiscoveryPivot] = field(default_factory=list)

    def add_pivot(self, pivot: DiscoveryPivot) -> bool:
        key = (pivot.kind, _norm(pivot.value))
        if not key[1] or key in self.seen_pivots:
            return False
        self.seen_pivots.add(key)
        self.pivots.append(pivot)
        return True

    def add_query(self, query: str) -> bool:
        key = _norm(query)
        if not key or key in self.seen_queries:
            return False
        self.seen_queries.add(key)
        return True


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _walk_scalars(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_scalars(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _walk_scalars(child)
    elif isinstance(value, (str, int)):
        text = _clean(value)
        if text:
            yield text


def pivots_from_offer(mission: ProductMission, offer: Offer) -> list[DiscoveryPivot]:
    """Extract possible control points from one discovered offer.

    This is intentionally recall-first.  A pivot is not proof of identity; it is
    permission to explore another graph.  The validator still decides whether an
    offer belongs to the target product.
    """

    source_ids = {_norm(v) for v in extract_identifiers(mission)}
    corpus = " ".join(
        [offer.title, offer.marketplace_product_id or "", offer.seller_name or "",
         *list(_walk_scalars(offer.attributes))]
    )
    candidates: list[DiscoveryPivot] = []

    # Product-like codes learned from the marketplace page can unlock exact web,
    # seller-catalogue, distributor, or another-marketplace searches.
    for token in re.findall(r"\b(?=[A-ZА-ЯІЇЄ0-9-]{4,}\b)(?=[A-ZА-ЯІЇЄ0-9-]*\d)[A-ZА-ЯІЇЄ0-9-]+\b", corpus.upper()):
        if _norm(token) not in source_ids:
            candidates.append(DiscoveryPivot(
                PivotKind.IDENTIFIER, token, "offer", 0.85,
                (f"learned from {offer.marketplace.value} offer",),
            ))

    if offer.marketplace_product_id:
        candidates.append(DiscoveryPivot(
            PivotKind.MARKETPLACE_CARD, offer.marketplace_product_id,
            offer.marketplace.value, 0.95,
            ("marketplace product/card id",),
        ))

    if offer.seller_name:
        candidates.append(DiscoveryPivot(
            PivotKind.SELLER, offer.seller_name, offer.marketplace.value, 0.65,
            ("seller can expose its catalogue or cross-market listings",),
        ))

    # Common structured attributes are often aliases or stronger identifiers than
    # the supplier title.  Do not assume they are true; use them to branch.
    for key, value in offer.attributes.items():
        key_norm = _norm(key)
        text = _clean(value)
        if not text:
            continue
        if key_norm in {"model", "mpn", "sku", "ean", "gtin", "vendorcode", "vendor_code", "code"}:
            candidates.append(DiscoveryPivot(
                PivotKind.IDENTIFIER, text, f"attribute:{key}", 0.9,
                ("structured product attribute",),
            ))
        elif key_norm in {"brand", "vendor", "manufacturer", "бренд", "виробник", "производитель"}:
            candidates.append(DiscoveryPivot(
                PivotKind.ATTRIBUTE, text, f"attribute:{key}", 0.7,
                ("brand/manufacturer can open catalogue/distributor graph",),
            ))

    unique: dict[tuple[PivotKind, str], DiscoveryPivot] = {}
    for pivot in candidates:
        unique.setdefault((pivot.kind, _norm(pivot.value)), pivot)
    return list(unique.values())


def queries_from_pivot(mission: ProductMission, pivot: DiscoveryPivot) -> list[str]:
    """Turn a control point into a small set of *different-route* queries."""

    ids = extract_identifiers(mission)
    strongest_id = ids[0] if ids else mission.article
    value = _clean(pivot.value)
    queries: list[str] = []

    def add(q: str) -> None:
        q = _clean(q)
        if q and _norm(q) not in {_norm(x) for x in queries}:
            queries.append(q)

    if pivot.kind == PivotKind.IDENTIFIER:
        add(value)
        add(f'"{value}"')
    elif pivot.kind == PivotKind.SELLER:
        # Pivot from product-first search to seller-first search.
        add(f'"{value}" "{strongest_id}"')
        for identifier in ids[1:3]:
            add(f'"{value}" "{identifier}"')
    elif pivot.kind == PivotKind.MARKETPLACE_CARD:
        add(value)
        add(f'"{value}" "{strongest_id}"')
    elif pivot.kind in {PivotKind.ATTRIBUTE, PivotKind.BRAND_MODEL, PivotKind.ALIAS}:
        add(f'"{value}" "{strongest_id}"')
        add(value)

    return queries[:4]


def next_pivot_queries(
    mission: ProductMission,
    offers: Iterable[Offer],
    state: DiscoveryState,
    *,
    max_pivots: int = 5,
    max_queries: int = 8,
) -> list[str]:
    """Choose new routes after a discovery round.

    Analogy: do not spend three questions asking for more description if one
    question can reveal a spouse and open an entirely different social graph.
    Here the equivalent control point may be a new MPN/EAN, seller, card id,
    manufacturer, importer, or alias.
    """

    learned: list[DiscoveryPivot] = []
    for offer in offers:
        for pivot in pivots_from_offer(mission, offer):
            if state.add_pivot(pivot):
                learned.append(pivot)

    learned.sort(key=lambda p: p.confidence, reverse=True)
    out: list[str] = []
    for pivot in learned[:max_pivots]:
        for query in queries_from_pivot(mission, pivot):
            if state.add_query(query):
                out.append(query)
                if len(out) >= max_queries:
                    return out
    return out
