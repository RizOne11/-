from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit, urlunsplit

import httpx

from puma_scouts.models import Marketplace, Offer, ProductMission, ScanHealth, ScanReport
from puma_scouts.query import generate_queries
from puma_scouts.scouts.base import MarketplaceScout
from puma_scouts.validator import validate_offer

_ROZETKA_HOSTS = {"rozetka.com.ua", "www.rozetka.com.ua", "hard.rozetka.com.ua"}
_PRODUCT_ID_RE = re.compile(r"/p(\d+)(?:/|$)", re.I)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).casefold())


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = re.sub(r"^/(?:ua|ru)/", "/", parts.path, flags=re.I)
    return urlunsplit(("https", host, path.rstrip("/"), "", ""))


def _product_id(url: str) -> str | None:
    match = _PRODUCT_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _is_rozetka_product_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.lower() in _ROZETKA_HOSTS and _product_id(url) is not None


def _decimal_price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = _clean(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.]", "", text)
    try:
        result = Decimal(text)
        return result if result > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _jsonld_products(html: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, flags=re.I | re.S):
        try:
            payload = json.loads(raw.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                items.extend(x for x in graph if isinstance(x, dict))
            if str(item.get("@type", "")).casefold() == "product":
                blocks.append(item)
    return blocks


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
    product_id = _product_id(url)
    if not product_id:
        return None
    title = _meta(html, "og:title") or ""
    price: Decimal | None = None
    availability: str | None = None
    attrs: dict[str, Any] = {}
    products = _jsonld_products(html)
    if products:
        product = products[0]
        title = _clean(product.get("name")) or title
        for key in ("sku", "mpn", "gtin", "gtin13", "brand", "model"):
            value = product.get(key)
            if value:
                attrs[key] = value
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            price = _decimal_price(offers.get("price") or offers.get("lowPrice"))
            availability = _clean(offers.get("availability")) or None
            seller = offers.get("seller")
            if isinstance(seller, dict):
                attrs["seller"] = seller.get("name")
    if not price:
        price = _decimal_price(_meta(html, "product:price:amount"))
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = _clean(re.sub(r"<[^>]+>", " ", match.group(1))) if match else ""
    if not title:
        return None
    return Offer(article=article, marketplace=Marketplace.ROZETKA, marketplace_product_id=product_id,
        seller_name=_clean(attrs.pop("seller", "")) or None, title=title, price=price,
        availability=availability, url=_canonical_url(url), image_urls=[], attributes=attrs,
        query_used=query, discovery_method="catalog/index->rozetka-product-page")


def _offer_from_api(article: str, product_id: str, payload: dict[str, Any], query: str, fallback_url: str) -> Offer | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    title = _clean(data.get("title") or data.get("name"))
    if not title:
        return None
    url = data.get("href") or data.get("url") or fallback_url
    price = _decimal_price(data.get("price") or data.get("price_pcs"))
    seller = data.get("seller")
    seller_name = None
    if isinstance(seller, dict):
        seller_name = _clean(seller.get("title") or seller.get("name")) or None
    elif data.get("seller_title"):
        seller_name = _clean(data.get("seller_title"))
    brand = data.get("brand")
    attrs: dict[str, Any] = {"source": "rozetka-product-api"}
    if isinstance(brand, dict):
        attrs["brand"] = brand.get("title") or brand.get("name")
    elif brand:
        attrs["brand"] = brand
    for key in ("sku", "mpn", "model"):
        if data.get(key):
            attrs[key] = data[key]
    availability = _clean(data.get("sell_status") or data.get("availability")) or None
    return Offer(article=article, marketplace=Marketplace.ROZETKA, marketplace_product_id=product_id,
        seller_name=seller_name, title=title, price=price, availability=availability,
        url=_canonical_url(str(url)) if _is_rozetka_product_url(str(url)) else _canonical_url(fallback_url),
        image_urls=[], attributes=attrs, query_used=query,
        discovery_method="catalog/index->rozetka-product-api")


def _extract_indexed_rozetka_urls(html: str) -> list[str]:
    text = html_lib.unescape(html).replace("\\u002F", "/").replace("\\/", "/")
    candidates: list[str] = []
    for raw in re.findall(r'https?://[^\s"\'<>]+', text, flags=re.I):
        raw = raw.rstrip(').,;]')
        parts = urlsplit(raw)
        if "duckduckgo.com" in parts.netloc and parts.path.startswith("/l/"):
            target = parse_qs(parts.query).get("uddg", [""])[0]
            raw = unquote(target) if target else raw
        if _is_rozetka_product_url(raw):
            candidates.append(_canonical_url(raw))
    decoded = unquote(text)
    for match in re.findall(r'(?:https?://)?(?:hard\.)?rozetka\.com\.ua/(?:ua/|ru/)?[^\s"\'<>]*?/p\d+/?', decoded, flags=re.I):
        raw = match if match.startswith("http") else "https://" + match
        if _is_rozetka_product_url(raw):
            candidates.append(_canonical_url(raw))
    unique: dict[str, str] = {}
    for url in candidates:
        pid = _product_id(url)
        if pid:
            unique.setdefault(pid, url)
    return list(unique.values())


def _rank_candidate_urls(urls: list[str], query: str) -> list[str]:
    needle = _norm(query)
    def rank(url: str) -> tuple[int, int]:
        slug = urlsplit(url).path.rsplit("/p", 1)[0]
        normalized_slug = _norm(slug)
        exact = 0 if needle and needle in normalized_slug else 1
        return (exact, len(normalized_slug))
    return sorted(urls, key=rank)


class RozetkaScout(MarketplaceScout):
    marketplace = Marketplace.ROZETKA

    def __init__(self, *, timeout: float = 15.0, max_candidates_per_query: int = 30) -> None:
        self.timeout = timeout
        self.max_candidates_per_query = max_candidates_per_query
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.5"}

    async def generate_queries(self, mission: ProductMission) -> list[str]:
        return generate_queries(mission)

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url, headers=self.headers, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def _catalog_candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        needle = _norm(query)
        if not needle:
            return []
        # Category pages are useful for marketplace offers that Rozetka's text search does not index.
        catalog_urls = [
            "https://hard.rozetka.com.ua/ua/monitors/c80089/producer=xiaomi/",
            "https://hard.rozetka.com.ua/ua/monitors/c80089/",
        ]
        found: dict[str, str] = {}
        for catalog_url in catalog_urls:
            try:
                page = await self._get(client, catalog_url)
            except httpx.HTTPError:
                continue
            decoded = html_lib.unescape(page).replace("\\u002F", "/").replace("\\/", "/")
            for url in _extract_indexed_rozetka_urls(decoded):
                pid = _product_id(url)
                if pid:
                    found.setdefault(pid, url)
            # Some catalog HTML contains product IDs separately from titles/URLs. Capture local context.
            lower_norm = _norm(decoded)
            if needle in lower_norm:
                for match in re.finditer(r"p\d+", decoded, flags=re.I):
                    start, end = max(0, match.start() - 1200), min(len(decoded), match.end() + 1200)
                    context = decoded[start:end]
                    if needle not in _norm(context):
                        continue
                    pid_match = re.search(r"p(\d+)", match.group(0), flags=re.I)
                    if pid_match:
                        pid = pid_match.group(1)
                        found.setdefault(pid, f"https://hard.rozetka.com.ua/ua/p{pid}/p{pid}/")
            exact = [u for u in found.values() if needle in _norm(u)]
            if exact:
                return _rank_candidate_urls(exact, query)[:self.max_candidates_per_query]
        return _rank_candidate_urls(list(found.values()), query)[:self.max_candidates_per_query]

    async def _api_offer(self, client: httpx.AsyncClient, article: str, url: str, query: str) -> Offer | None:
        pid = _product_id(url)
        if not pid:
            return None
        api_url = f"https://product-api.rozetka.com.ua/v4/goods/get-main?front-type=xl&country=UA&lang=ua&goodsId={pid}"
        response = await client.get(api_url, headers=self.headers, follow_redirects=True)
        response.raise_for_status()
        return _offer_from_api(article, pid, response.json(), query, url)

    async def _indexed_candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        searches = [f'site:rozetka.com.ua "{query}"', f'site:hard.rozetka.com.ua "{query}"', f'site:rozetka.com.ua {query}']
        found: dict[str, str] = {}
        last_error: httpx.HTTPError | None = None
        for search in searches:
            q = quote_plus(search)
            engines = [f"https://html.duckduckgo.com/html/?q={q}", f"https://www.google.com/search?q={q}&num=20&filter=0", f"https://www.bing.com/search?q={q}&count=20"]
            for search_url in engines:
                try:
                    page = await self._get(client, search_url)
                except httpx.HTTPError as exc:
                    last_error = exc
                    continue
                for url in _rank_candidate_urls(_extract_indexed_rozetka_urls(page), query):
                    pid = _product_id(url)
                    if pid:
                        found.setdefault(pid, url)
                exact = [url for url in found.values() if _norm(query) in _norm(urlsplit(url).path)]
                if exact:
                    return _rank_candidate_urls(exact, query)[:self.max_candidates_per_query]
        catalog = await self._catalog_candidate_urls(client, query)
        for url in catalog:
            pid = _product_id(url)
            if pid:
                found.setdefault(pid, url)
        if found:
            return _rank_candidate_urls(list(found.values()), query)[:self.max_candidates_per_query]
        if last_error:
            raise last_error
        return []

    async def _candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        search_url = f"https://rozetka.com.ua/ua/search/?text={quote_plus(query)}"
        try:
            page = await self._get(client, search_url)
            urls: list[str] = []
            seen: set[str] = set()
            for href in re.findall(r'href=["\']([^"\']+)["\']', page, flags=re.I):
                absolute = urljoin(search_url, href.replace("&amp;", "&"))
                if not _is_rozetka_product_url(absolute):
                    continue
                pid = _product_id(absolute)
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                urls.append(_canonical_url(absolute))
            if urls:
                return _rank_candidate_urls(urls, query)[:self.max_candidates_per_query]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 429):
                raise
        return await self._indexed_candidate_urls(client, query)

    async def _fetch_offer(self, client: httpx.AsyncClient, mission: ProductMission, url: str, query: str) -> Offer | None:
        try:
            page = await self._get(client, url)
            offer = _offer_from_page(mission.article, url, page, query)
            if offer:
                return offer
        except httpx.HTTPError:
            pass
        return await self._api_offer(client, mission.article, url, query)

    async def discover(self, mission: ProductMission, query: str) -> list[Offer]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            candidates = await self._candidate_urls(client, query)
            offers: list[Offer] = []
            for url in candidates:
                try:
                    offer = await self._fetch_offer(client, mission, url, query)
                    if offer:
                        offers.append(offer)
                except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                    continue
            return offers

    async def scan(self, mission: ProductMission) -> ScanReport:
        queries = await self.generate_queries(mission)
        errors: list[str] = []
        collected: list[Offer] = []
        pages_scanned = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query in queries:
                try:
                    candidates = await self._candidate_urls(client, query)
                except httpx.HTTPError as exc:
                    errors.append(f"discovery {query!r}: {type(exc).__name__}: {exc}")
                    continue
                for url in candidates:
                    pages_scanned += 1
                    try:
                        offer = await self._fetch_offer(client, mission, url, query)
                        if offer:
                            collected.append(offer)
                    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                        errors.append(f"page/api {url}: {type(exc).__name__}: {exc}")
                    await asyncio.sleep(0)
        unique: dict[str, Offer] = {}
        for offer in collected:
            key = offer.marketplace_product_id or str(offer.url)
            unique.setdefault(key, offer)
        validated = [validate_offer(mission, offer) for offer in unique.values()]
        if validated:
            health = ScanHealth.PARTIAL if errors else ScanHealth.FOUND
        elif errors:
            health = ScanHealth.ACCESS_LIMITED
        else:
            health = ScanHealth.NOT_FOUND
        return ScanReport(article=mission.article, marketplace=self.marketplace, health=health,
            queries_generated=len(queries), pages_scanned=pages_scanned, candidates_seen=len(collected),
            candidates_collected=len(unique), duplicates_removed=max(0, len(collected) - len(unique)),
            search_rounds=1, errors=errors, offers=validated)
