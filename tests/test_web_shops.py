from decimal import Decimal

from puma_scouts.models import ProductMission
from puma_scouts.scouts.web_shops import WebShopsScout


def test_search_links_keep_ua_shop_and_drop_noise():
    scout=WebShopsScout()
    page='''<a href="https://wondertech.ua/ua/monitors/redmi-a27q">купити 9 999 грн</a>
    <a href="https://market.yandex.ru/card/x">купить</a>
    <a href="https://startech.com.bd/xiaomi">buy</a>
    <a href="https://prom.ua/ua/p123">купити</a>'''
    links=scout._search_links(page,"https://search.brave.com/search?q=x")
    assert links == ["https://wondertech.ua/ua/monitors/redmi-a27q"]


def test_offer_requires_product_jsonld_and_price():
    scout=WebShopsScout()
    mission=ProductMission(article="SRC-1",source_data={"name":"Xiaomi Redmi A27Q 2025","brand":"Xiaomi","model":"P27QCB-RA"})
    page='''<script type="application/ld+json">{"@type":"Product","name":"Xiaomi Redmi A27Q 2025 P27QCB-RA","model":"P27QCB-RA","brand":{"name":"Xiaomi"},"offers":{"@type":"Offer","price":"9299","priceCurrency":"UAH","availability":"InStock"}}</script>'''
    offer=scout._offer(mission,"https://minipk.com.ua/monitory/redmi-a27q",page,"xiaomi купити грн")
    assert offer is not None
    assert offer.price == Decimal("9299")
    assert offer.attributes["source_domain"] == "minipk.com.ua"


def test_offer_rejects_content_page_without_price():
    scout=WebShopsScout()
    mission=ProductMission(article="SRC-1",source_data={"name":"Xiaomi Redmi A27Q 2025"})
    page='''<script type="application/ld+json">{"@type":"Product","name":"Xiaomi Redmi A27Q 2025"}</script>'''
    assert scout._offer(mission,"https://example.ua/review",page,"q") is None
