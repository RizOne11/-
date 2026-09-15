from puma_scouts.models import ProductMission, Verdict
from puma_scouts.scouts.rozetka import _canonical_url, _offer_from_page, _product_id
from puma_scouts.validator import validate_offer


def test_rozetka_locale_urls_share_product_id():
    ua = "https://rozetka.com.ua/ua/xiaomi-p27qcb-ra/p123456789/?utm_source=x"
    ru = "https://rozetka.com.ua/ru/xiaomi-p27qcb-ra/p123456789/"
    assert _product_id(ua) == _product_id(ru) == "123456789"
    assert _canonical_url(ua) == _canonical_url(ru)


def test_jsonld_product_page_becomes_offer():
    html = '''
    <html><head>
      <meta property="og:title" content="Монітор Xiaomi Gaming Monitor G27Qi P27QCB-RA">
      <script type="application/ld+json">
      {"@type":"Product","name":"Xiaomi Gaming Monitor G27Qi P27QCB-RA",
       "mpn":"P27QCB-RA","brand":"Xiaomi",
       "offers":{"@type":"Offer","price":"9999","priceCurrency":"UAH","availability":"https://schema.org/InStock"}}
      </script>
    </head></html>
    '''
    offer = _offer_from_page(
        "SUP-001",
        "https://rozetka.com.ua/ua/xiaomi-g27qi/p123456789/",
        html,
        "P27QCB-RA",
    )
    assert offer is not None
    assert offer.marketplace_product_id == "123456789"
    assert str(offer.price) == "9999"
    assert offer.attributes["mpn"] == "P27QCB-RA"

    mission = ProductMission(
        article="SUP-001",
        source_data={"brand":"Xiaomi","name":"Xiaomi Gaming Monitor G27Qi","mpn":"P27QCB-RA"},
    )
    validated = validate_offer(mission, offer)
    assert validated.verdict == Verdict.PASS
