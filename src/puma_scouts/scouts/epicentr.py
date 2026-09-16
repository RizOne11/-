from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

import httpx

from puma_scouts.models import Marketplace, Offer, ProductMission, ScanHealth, ScanReport, Verdict
from puma_scouts.query import generate_queries
from puma_scouts.scouts.base import MarketplaceScout
from puma_scouts.validator import validate_offer

_EPICENTR_HOSTS = {"epicentrk.ua", "www.epicentrk.ua"}
_PRODUCT_PATH_RE = re.compile(r"/(?:ua/)?(?:shop|shop-mplc)/[^?#]+\.html$", re.I)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    path = re.sub(r"^/ua/", "/ua/", parts.path, flags=re.I)
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def _is_product_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.lower() in _EPICENTR_HOSTS and bool(_PRODUCT_PATH_RE.search(parts.path))


def _decimal_price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = _clean(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.]", "", text)
    try:
        price = Decimal(text)
        return price if price > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _jsonld_products(html: str) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try:
            payload = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        queue = payload if isinstance(payload, list) else [payload]
        for item in queue:
            if not isinstance(item, dict):
                continue
            if str(item.get("@type", "")).casefold() == "product":
                products.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                products.extend(x for x in graph if isinstance(x, dict) and str(x.get("@type", "")).casefold() == "product")
    return products


def _meta(html: str, key: str) -> str | None:
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return _clean(match.group(1))
    return None


def _offer_from_page(article: str, url: str, html: str, query: str) -> Offer | None:
    title = _meta(html, "og:title") or ""
    price = _decimal_price(_meta(html, "product:price:amount"))
    availability: str | None = None
    seller_name: str | None = None
    attrs: dict[str, Any] = {"source": "epicentr-product-page"}

    products = _jsonld_products(html)
    if products:
        product = products[0]
        title = _clean(product.get("name")) or title
        for key in ("sku", "mpn", "gtin", "gtin13", "model"):
            if product.get(key):
                attrs[key] = product[key]
        brand = product.get("brand")
        if isinstance(brand, dict):
            attrs["brand"] = brand.get("name")
        elif brand:
            attrs["brand"] = brand
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            price = price or _decimal_price(offers.get("price") or offers.get("lowPrice"))
            availability = _clean(offers.get("availability")) or None
            seller = offers.get("seller")
            if isinstance(seller, dict):
                seller_name = _clean(seller.get("name")) or None

    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = _clean(re.sub(r"<[^>]+>", " ", match.group(1))) if match else ""
    if not title:
        return None

    if not seller_name:
        match = re.search(r"(?:Продавець товару|Продавец|Продавець)\s*:?</?[^>]*>?(?:\s*<[^>]+>)*\s*([^<\n]{2,100})", html, flags=re.I)
        if match:
            seller_name = _clean(match.group(1)) or None

    product_id = None
    code = re.search(r"(?:КОД|Код)\s*(?:&nbsp;|\s)*([A-ZА-ЯІЇЄ0-9-]{5,})", html, flags=re.I)
    if code:
        product_id = code.group(1)

    return Offer(
        article=article,
        marketplace=Marketplace.EPICENTR,
        marketplace_product_id=product_id,
        seller_name=seller_name,
        title=title,
        price=price,
        availability=availability,
        url=_canonical_url(url),
        image_urls=[],
        attributes=attrs,
        query_used=query,
        discovery_method="epicentr-search->product-page",
    )


class EpicentrScout(MarketplaceScout):
    marketplace = Marketplace.EPICENTR

    def __init__(self, *, timeout: float = 15.0, max_candidates_per_query: int = 30) -> None:
        self.timeout = timeout
        self.max_candidates_per_query = max_candidates_per_query
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
            "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.5",
        }

    async def generate_queries(self, mission: ProductMission) -> list[str]:
        # Deliberately do not make the supplier article the primary discovery key.
        queries = generate_queries(mission)
        article = mission.article.casefold().strip()
        non_article = [q for q in queries if q.casefold().strip() != article]
        return non_article or queries

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url, headers=self.headers, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def _candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        search_urls = [
            f"https://epicentrk.ua/ua/search/?q={quote_plus(query)}",
            f"https://epicentrk.ua/ua/search/?search={quote_plus(query)}",
        ]
        found: dict[str, str] = {}
        last_error: httpx.HTTPError | None = None
        for search_url in search_urls:
            try:
                page = await self._get(client, search_url)
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            decoded = html_lib.unescape(page).replace("\\/", "/")
            for href in re.findall(r'href=["\']([^"\']+)["\']', decoded, flags=re.I):
                absolute = urljoin(search_url, href)
                if _is_product_url(absolute):
                    found.setdefault(_canonical_url(absolute), _canonical_url(absolute))
            for raw in re.findall(r'https?://(?:www\.)?epicentrk\.ua/(?:ua/)?(?:shop|shop-mplc)/[^\s"\'<>]+?\.html', decoded, flags=re.I):
                if _is_product_url(raw):
                    found.setdefault(_canonical_url(raw), _canonical_url(raw))
            if found:
                break
        if found:
            return list(found.values())[: self.max_candidates_per_query]
        if last_error:
            raise last_error
        return []

    async def discover(self, mission: ProductMission, query: str) -> list[Offer]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            urls = await self._candidate_urls(client, query)
            offers: list[Offer] = []
            for url in urls:
                try:
                    page = await self._get(client, url)
                    offer = _offer_from_page(mission.article, url, page, query)
                    if offer:
                        offers.append(offer)
                except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                    continue
            return offers

    async def scan(self, mission: ProductMission) -> ScanReport:
        queries = await self.generate_queries(mission)
        unique: dict[str, Offer] = {}
        errors: list[str] = []
        seen = 0
        for query in queries:
            try:
                offers = await self.discover(mission, query)
            except Exception as exc:
                errors.append(f"search {query!r}: {type(exc).__name__}: {exc}")
                continue
            seen += len(offers)
            for offer in offers:
                key = str(offer.url)
                unique.setdefault(key, offer)

        validated = [validate_offer(mission, offer) for offer in unique.values()]
        passes = [item for item in validated if item.verdict == Verdict.PASS]
        conflicts = [item for item in validated if item.verdict == Verdict.CONFLICT]
        if passes:
            health = ScanHealth.PARTIAL if errors else ScanHealth.FOUND
        elif conflicts:
            health = ScanHealth.PARTIAL
        elif errors and not validated:
            health = ScanHealth.ACCESS_LIMITED
        else:
            health = ScanHealth.NOT_FOUND

        return ScanReport(
            article=mission.article,
            marketplace=self.marketplace,
            health=health,
            queries_generated=len(queries),
            pages_scanned=len(unique),
            candidates_seen=seen,
            candidates_collected=len(unique),
            duplicates_removed=max(0, seen - len(unique)),
            search_rounds=len(queries),
            errors=errors,
            offers=validated,
        )
