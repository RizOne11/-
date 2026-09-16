from decimal import Decimal

from puma_scouts.models import Marketplace, ProductMission
from puma_scouts.scouts.epicentr import EpicentrScout, _offer_from_page


def test_epicentr_product_page_parses_market_data():
    html = '''
    <html><head>
      <meta property="og:title" content="Монітор Redmi A27Q 2025 27&quot;">
      <script type="application/ld+json">
      {"@type":"Product","name":"Монітор Redmi A27Q 2025 27\"","brand":{"@type":"Brand","name":"Redmi"},
       "model":"P27QCB-RA","offers":{"@type":"Offer","price":"9298","availability":"https://schema.org/InStock","seller":{"@type":"Organization","name":"Wondertech"}}}
      </script>
    </head><body>КОД MP33387235</body></html>
    '''
    offer = _offer_from_page(
        "SUPPLIER-ARTICLE",
        "https://epicentrk.ua/ua/shop/mplc-monitor-redmi-a27q-test.html",
        html,
        "Redmi A27Q 2025",
    )
    assert offer is not None
    assert offer.marketplace == Marketplace.EPICENTR
    assert offer.price == Decimal("9298")
    assert offer.seller_name == "Wondertech"
    assert offer.attributes["model"] == "P27QCB-RA"
    assert offer.image_urls == []


def test_epicentr_does_not_prioritize_supplier_article():
    mission = ProductMission(
        article="HUBBER-12345",
        source_data={"name": "Монітор Redmi A27Q 2025", "brand": "Redmi", "model": "P27QCB-RA"},
    )
    import asyncio
    queries = asyncio.run(EpicentrScout().generate_queries(mission))
    assert queries
    assert queries[0] != mission.article
    assert mission.article not in queries
    assert any("Redmi" in query or "P27QCB-RA" in query for query in queries)
