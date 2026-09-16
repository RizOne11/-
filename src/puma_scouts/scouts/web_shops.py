from __future__ import annotations

import html as html_lib
from collections import defaultdict
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from puma_scouts.models import Marketplace, Offer, ProductMission, ScanHealth, ScanReport, Verdict
from puma_scouts.query import generate_queries
from puma_scouts.scouts.catalog import _canonical, _jsonld_products, _clean, _price
from puma_scouts.scouts.base import MarketplaceScout
from puma_scouts.validator import validate_offer


class WebShopsScout(MarketplaceScout):
    """Discover independent Ukrainian shop product pages via free web search."""

    marketplace = Marketplace.WEB_SHOPS
    blocked_domains = (
        "prom.ua", "epicentrk.ua", "hotline.ua", "rozetka.com.ua", "zakupka.com",
        "allo.ua", "comfy.ua", "foxtrot.com.ua", "kasta.ua", "google.com",
        "bing.com", "duckduckgo.com", "brave.com", "yandex.ru", "yandex.com",
        "youtube.com", "facebook.com", "instagram.com", "tiktok.com", "hackerone.com",
    )

    def __init__(self, *, timeout: float = 15.0, max_candidates_per_query: int = 20, max_per_domain: int = 2) -> None:
        self.timeout = timeout
        self.max_candidates_per_query = max_candidates_per_query
        self.max_per_domain = max_per_domain
        self.headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36","Accept-Language":"uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.5"}

    async def generate_queries(self, mission: ProductMission) -> list[str]:
        article = mission.article.casefold().strip()
        out=[]
        for q in generate_queries(mission):
            q=q.strip()
            if not q or q.casefold()==article or (article and article in q.casefold()): continue
            if q not in out: out.append(q)
        return [f'{q} купити грн' for q in out[:5]]

    def _blocked(self, host: str) -> bool:
        host=host.casefold().removeprefix("www.")
        return any(host==x or host.endswith("."+x) for x in self.blocked_domains)

    def _unwrap(self, href: str, base: str) -> str:
        absolute=urljoin(base,html_lib.unescape(href)); p=urlsplit(absolute)
        if "duckduckgo.com" in p.netloc and p.path.startswith("/l/"):
            wrapped=parse_qs(p.query).get("uddg",[])
            if wrapped: absolute=unquote(wrapped[0])
        return absolute

    def _search_links(self, page: str, base: str) -> list[str]:
        result=[]; per_domain=defaultdict(int)
        for a in BeautifulSoup(page,"html.parser").find_all("a",href=True):
            url=self._unwrap(a["href"],base); p=urlsplit(url); host=p.netloc.casefold().removeprefix("www.")
            if p.scheme not in ("http","https") or not host or self._blocked(host) or not p.path or p.path=="/": continue
            text=(a.get_text(" ",strip=True)+" "+url).casefold()
            uaish=host.endswith(".ua") or any(x in text for x in ("купити","ціна","грн","uah","україн"))
            if not uaish or per_domain[host]>=self.max_per_domain: continue
            canonical=_canonical(url)
            if canonical not in result:
                result.append(canonical); per_domain[host]+=1
            if len(result)>=self.max_candidates_per_query: break
        return result

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        r=await client.get(url,headers=self.headers,follow_redirects=True); r.raise_for_status(); return r.text

    async def _candidate_urls(self, client: httpx.AsyncClient, query: str) -> list[str]:
        found=[]
        engines=(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            f"https://www.bing.com/search?q={quote_plus(query)}&count=30&setlang=uk",
            f"https://search.brave.com/search?q={quote_plus(query)}&source=web",
        )
        for search_url in engines:
            try: page=await self._get(client,search_url)
            except httpx.HTTPError: continue
            for url in self._search_links(page,search_url):
                if url not in found: found.append(url)
                if len(found)>=self.max_candidates_per_query: return found
        return found

    def _offer(self, mission: ProductMission, url: str, page: str, query: str) -> Offer | None:
        products=_jsonld_products(page)
        if not products: return None
        product=products[0]; title=_clean(product.get("name"))
        offers=product.get("offers")
        if isinstance(offers,list): offers=offers[0] if offers else None
        amount=availability=None
        if isinstance(offers,dict):
            amount=_price(offers.get("price") or offers.get("lowPrice")); availability=_clean(offers.get("availability")) or None
        if not title or amount is None: return None
        host=urlsplit(url).netloc.casefold().removeprefix("www.")
        attrs={"source":"web-shop-jsonld","source_domain":host}
        for key in ("sku","mpn","gtin","gtin13","model"):
            if product.get(key): attrs[key]=product[key]
        brand=product.get("brand")
        if isinstance(brand,dict): attrs["brand"]=brand.get("name")
        elif brand: attrs["brand"]=brand
        return Offer(article=mission.article,marketplace=self.marketplace,marketplace_product_id=_clean(product.get("sku")) or None,title=title,price=amount,availability=availability,url=_canonical(url),attributes=attrs,query_used=query,discovery_method="free-web-search->shop-jsonld")

    async def discover(self, mission: ProductMission, query: str) -> list[Offer]:
        """Discover and parse independent-shop offers for one generated query."""
        offers=[]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for url in await self._candidate_urls(client, query):
                try:
                    offer=self._offer(mission, url, await self._get(client, url), query)
                except httpx.HTTPError:
                    continue
                if offer:
                    offers.append(offer)
        return offers

    async def scan(self, mission: ProductMission) -> ScanReport:
        queries=await self.generate_queries(mission); unique={}; errors=[]; seen=0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query in queries:
                try: urls=await self._candidate_urls(client,query)
                except Exception as exc: errors.append(f"search {query!r}: {type(exc).__name__}: {exc}"); continue
                seen+=len(urls)
                for url in urls:
                    if url in unique: continue
                    try:
                        offer=self._offer(mission,url,await self._get(client,url),query)
                        if offer: unique[url]=offer
                    except httpx.HTTPError: continue
        validated=[validate_offer(mission,o) for o in unique.values()]
        passes=[x for x in validated if x.verdict==Verdict.PASS]; conflicts=[x for x in validated if x.verdict==Verdict.CONFLICT]
        health=ScanHealth.FOUND if passes and not errors else ScanHealth.PARTIAL if passes or conflicts else ScanHealth.ACCESS_LIMITED if errors and not validated else ScanHealth.NOT_FOUND
        return ScanReport(article=mission.article,marketplace=self.marketplace,health=health,queries_generated=len(queries),pages_scanned=len(unique),candidates_seen=seen,candidates_collected=len(unique),duplicates_removed=max(0,seen-len(unique)),search_rounds=len(queries),errors=errors,offers=validated)
