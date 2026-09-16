from __future__ import annotations

import html as html_lib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from puma_scouts.models import Marketplace, Offer, ProductMission, ScanHealth, ScanReport, Verdict
from puma_scouts.query import generate_queries
from puma_scouts.scouts.base import MarketplaceScout
from puma_scouts.validator import validate_offer


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(str(value or ""))).strip()


def _price(value: Any) -> Decimal | None:
    text = _clean(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.]", "", text)
    try:
        result = Decimal(text)
        return result if result > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _canonical(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit(("https", p.netloc.lower(), p.path, "", ""))


def _jsonld_products(page: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page, "html.parser")
    found: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        queue = payload if isinstance(payload, list) else [payload]
        for item in queue:
            if not isinstance(item, dict):
                continue
            if str(item.get("@type", "")).casefold() == "product": found.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list): found.extend(x for x in graph if isinstance(x, dict) and str(x.get("@type", "")).casefold() == "product")
    return found


class CatalogScout(MarketplaceScout):
    marketplace: Marketplace
    host: str
    search_templates: tuple[str, ...]
    product_path_hints: tuple[str, ...] = ()
    allow_subdomains = False

    def __init__(self, *, timeout: float = 15.0, max_candidates_per_query: int = 20) -> None:
        self.timeout = timeout
        self.max_candidates_per_query = max_candidates_per_query
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36", "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.5"}

    async def generate_queries(self, mission: ProductMission) -> list[str]:
        article = mission.article.casefold().strip()
        useful: list[str] = []
        for query in generate_queries(mission):
            q = query.strip()
            if not q or q.casefold() == article or (article and article in q.casefold()): continue
            if re.fullmatch(r"(?:19|20)\d{2}", q): continue
            if q not in useful: useful.append(q)
        return useful

    def _host_matches(self, candidate_host: str) -> bool:
        expected = self.host.casefold().removeprefix("www.")
        actual = candidate_host.casefold().removeprefix("www.")
        return actual == expected or (self.allow_subdomains and actual.endswith("." + expected))

    def _is_candidate(self, url: str) -> bool:
        p = urlsplit(url)
        if not self._host_matches(p.netloc) or not p.path or p.path == "/": return False
        if self.product_path_hints and not any(hint in p.path.casefold() for hint in self.product_path_hints): return False
        return True

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        r = await client.get(url, headers=self.headers, follow_redirects=True)
        r.raise_for_status()
        return r.text

    def _extract_candidate_links(self, page: str, base_url: str) -> list[str]:
        found: dict[str, None] = {}
        soup = BeautifulSoup(page, "html.parser")
        for tag in soup.find_all("a", href=True):
            absolute = urljoin(base_url, tag["href"])
            if self._is_candidate(absolute): found.setdefault(_canonical(absolute), None)
        # JS-heavy shops often serialize product URLs in hydration JSON rather than anchors.
        decoded = html_lib.unescape(page).replace("\\/", "/")
        patterns = [
            r'https?://[^"\'<>\\\s]+',
            r'(?P<path>/(?:ua|uk|ru)?/?[^"\'<>\\\s]{4,}\.html(?:\?[^"\'<>\\\s]*)?)',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, decoded, flags=re.I):
                raw = match.groupdict().get("path") or match.group(0)
                absolute = urljoin(base_url, raw.rstrip(".,);]"))
                if self._is_candidate(absolute): found.setdefault(_canonical(absolute), None)
                if len(found) >= self.max_candidates_per_query: break
        return list(found)[: self.max_candidates_per_query]

    async def _candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        found: dict[str, None] = {}
        for template in self.search_templates:
            search_url = template.format(q=quote_plus(query))
            try: page = await self._get(client, search_url)
            except httpx.HTTPError: continue
            for url in self._extract_candidate_links(page, search_url):
                found.setdefault(url, None)
                if len(found) >= self.max_candidates_per_query: return list(found)
        return list(found)

    def _offer(self, mission: ProductMission, url: str, page: str, query: str) -> Offer | None:
        products = _jsonld_products(page)
        if not products: return None
        product = products[0]
        title = _clean(product.get("name"))
        if not title: return None
        attrs: dict[str, Any] = {"source": f"{self.marketplace.value}-jsonld"}
        for key in ("sku", "mpn", "gtin", "gtin13", "model"):
            if product.get(key): attrs[key] = product[key]
        brand = product.get("brand")
        if isinstance(brand, dict): attrs["brand"] = brand.get("name")
        elif brand: attrs["brand"] = brand
        offers = product.get("offers")
        if isinstance(offers, list): offers = offers[0] if offers else None
        amount = availability = seller = None
        if isinstance(offers, dict):
            amount = _price(offers.get("price") or offers.get("lowPrice"))
            availability = _clean(offers.get("availability")) or None
            raw_seller = offers.get("seller")
            if isinstance(raw_seller, dict): seller = _clean(raw_seller.get("name")) or None
        return Offer(article=mission.article, marketplace=self.marketplace, marketplace_product_id=_clean(product.get("sku")) or None,
            seller_name=seller, title=title, price=amount, availability=availability, url=_canonical(url), image_urls=[], attributes=attrs,
            query_used=query, discovery_method=f"{self.marketplace.value}-search->jsonld")

    async def discover(self, mission: ProductMission, query: str) -> list[Offer]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            urls = await self._candidate_urls(client, query)
            result: list[Offer] = []
            for url in urls:
                try:
                    offer = self._offer(mission, url, await self._get(client, url), query)
                    if offer: result.append(offer)
                except httpx.HTTPError: continue
            return result

    async def scan(self, mission: ProductMission) -> ScanReport:
        queries = await self.generate_queries(mission)
        unique: dict[str, Offer] = {}; errors: list[str] = []; seen = 0
        for query in queries:
            try: offers = await self.discover(mission, query)
            except Exception as exc:
                errors.append(f"search {query!r}: {type(exc).__name__}: {exc}"); continue
            seen += len(offers)
            for offer in offers: unique.setdefault(str(offer.url), offer)
        validated = [validate_offer(mission, offer) for offer in unique.values()]
        passes = [x for x in validated if x.verdict == Verdict.PASS]
        conflicts = [x for x in validated if x.verdict == Verdict.CONFLICT]
        health = ScanHealth.FOUND if passes and not errors else ScanHealth.PARTIAL if passes or conflicts else ScanHealth.ACCESS_LIMITED if errors and not validated else ScanHealth.NOT_FOUND
        return ScanReport(article=mission.article, marketplace=self.marketplace, health=health, queries_generated=len(queries), pages_scanned=len(unique), candidates_seen=seen,
            candidates_collected=len(unique), duplicates_removed=max(0, seen-len(unique)), search_rounds=len(queries), errors=errors, offers=validated)


class PromScout(CatalogScout):
    marketplace = Marketplace.PROM; host = "prom.ua"; allow_subdomains = True; product_path_hints = ("/p", "/m")
    search_templates = ("https://prom.ua/ua/search?search_term={q}", "https://prom.ua/ua/search?search_term={q}&sort=score")

class AlloScout(CatalogScout):
    marketplace = Marketplace.ALLO; host = "allo.ua"; product_path_hints = (".html",)
    search_templates = ("https://allo.ua/ua/catalogsearch/result/?q={q}", "https://allo.ua/ua/catalogsearch/result/?q={q}&cat=")

class FoxtrotScout(CatalogScout):
    marketplace = Marketplace.FOXTROT; host = "foxtrot.com.ua"; product_path_hints = ("/shop/", ".html")
    search_templates = ("https://www.foxtrot.com.ua/uk/search?query={q}", "https://www.foxtrot.com.ua/uk/search?search={q}")

class ComfyScout(CatalogScout):
    marketplace = Marketplace.COMFY; host = "comfy.ua"; product_path_hints = (".html",)
    search_templates = ("https://comfy.ua/ua/search/?q={q}", "https://comfy.ua/ua/search?q={q}")

class KastaScout(CatalogScout):
    marketplace = Marketplace.KASTA; host = "kasta.ua"; allow_subdomains = True
    search_templates = ("https://kasta.ua/uk/search/?q={q}", "https://kasta.ua/uk/search?q={q}")

class HotlineScout(CatalogScout):
    marketplace = Marketplace.HOTLINE; host = "hotline.ua"
    search_templates = ("https://hotline.ua/ua/sr/?q={q}",)
