from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Marketplace(StrEnum):
    PROM = "prom"
    ROZETKA = "rozetka"
    EPICENTR = "epicentr"


class Verdict(StrEnum):
    PASS = "PASS"
    CONFLICT = "CONFLICT"
    REJECT = "REJECT"


class ScanHealth(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    PARTIAL = "PARTIAL"
    ACCESS_LIMITED = "ACCESS_LIMITED"
    SCOUT_ERROR = "SCOUT_ERROR"
    PARSER_ERROR = "PARSER_ERROR"


class ProductMission(BaseModel):
    """One immutable user article plus every source field available for it."""

    article: str = Field(min_length=1)
    source_data: dict[str, Any] = Field(default_factory=dict)


class Offer(BaseModel):
    article: str
    marketplace: Marketplace
    marketplace_product_id: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    title: str
    price: Decimal | None = None
    old_price: Decimal | None = None
    currency: str = "UAH"
    availability: str | None = None
    url: HttpUrl
    image_urls: list[HttpUrl] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    query_used: str | None = None
    discovery_method: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scan_id: str | None = None


class ValidatedOffer(BaseModel):
    offer: Offer
    verdict: Verdict
    score: float = Field(ge=0, le=1)
    positive_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


class ScanReport(BaseModel):
    article: str
    marketplace: Marketplace
    health: ScanHealth
    queries_generated: int = 0
    pages_scanned: int = 0
    candidates_seen: int = 0
    candidates_collected: int = 0
    duplicates_removed: int = 0
    new_identifiers_found: list[str] = Field(default_factory=list)
    search_rounds: int = 0
    errors: list[str] = Field(default_factory=list)
    offers: list[ValidatedOffer] = Field(default_factory=list)
