from puma_scouts.models import Marketplace, Offer, ProductMission, Verdict
from puma_scouts.validator import validate_offer


def _mission():
    return ProductMission(article="CONTROL-NOT-A-SEARCH-KEY", source_data={"name": "Монітор Redmi A27Q 2025", "brand": "Redmi", "model": "P27QCB-RA"})


def _offer(title: str, marketplace=Marketplace.EPICENTR) -> Offer:
    host = "prom.ua" if marketplace == Marketplace.PROM else "epicentrk.ua"
    return Offer(article="CONTROL-NOT-A-SEARCH-KEY", marketplace=marketplace, title=title, url=f"https://{host}/test.html")


def test_exact_model_passes():
    offer = _offer("Монітор Redmi A27Q 2025 P27QCB-RA 27 дюймів"); offer.attributes={"model":"P27QCB-RA","brand":"Redmi"}
    assert validate_offer(_mission(),offer).verdict==Verdict.PASS


def test_family_name_does_not_pass_wrong_generation():
    offer=_offer("Монітор Redmi A27Q 2026 27 дюймів"); offer.attributes={"model":"A27Q","brand":"Redmi"}; result=validate_offer(_mission(),offer)
    assert result.verdict!=Verdict.PASS


def test_bundle_with_family_name_does_not_pass():
    offer=_offer("Комплект ПК Ryzen 5 + монітор Redmi A27Q"); offer.attributes={"brand":"Redmi"}
    assert validate_offer(_mission(),offer).verdict!=Verdict.PASS


def test_watch_strap_does_not_pass_as_watch_case():
    mission=ProductMission(article="FAKE",source_data={"name":"Дорожный футляр для часов Clockhouse 97x157x68 мм Коричневый","brand":"Clockhouse"}); result=validate_offer(mission,_offer("Ремешок из натуральной кожи для смарт-часов Clockhouse 22 мм Коричневый"))
    assert result.verdict!=Verdict.PASS


def test_single_gas_can_does_not_pass_as_twenty_pack():
    mission=ProductMission(article="FAKE",source_data={"name":"Баллон газовый универсальный X-Treme 227 г 20 шт","brand":"X-Treme"}); result=validate_offer(mission,_offer("Баллон газовый X-Treme пропан-бутан 227 г"))
    assert result.verdict!=Verdict.PASS


def test_wrong_brand_same_pack_does_not_pass():
    mission=ProductMission(article="FAKE",source_data={"name":"Баллон газовый универсальный X-Treme 227 г 20 шт","brand":"X-Treme"}); result=validate_offer(mission,_offer("Баллон универсальный газовый WINSO 220 г 20 шт"))
    assert result.verdict!=Verdict.PASS


def test_wrong_numeric_spec_same_brand_does_not_pass():
    mission=ProductMission(article="FAKE",source_data={"name":"Баллон газовый универсальный X-Treme 227 г 20 шт","brand":"X-Treme"}); result=validate_offer(mission,_offer("Баллон газовый универсальный X-Treme 220 г 20 шт"))
    assert result.verdict!=Verdict.PASS


def test_exact_brand_quantity_and_numeric_spec_can_pass():
    mission=ProductMission(article="FAKE",source_data={"name":"Баллон газовый универсальный X-Treme 227 г 20 шт","brand":"X-Treme"})
    assert validate_offer(mission,_offer("Баллон газовый универсальный X-Treme 227 г 20 шт")).verdict==Verdict.PASS


def test_multiword_model_requires_all_model_tokens():
    mission=ProductMission(article="FAKE",source_data={"name":"Система энергообеспечения EcoFlow STREAM Ultra X","brand":"EcoFlow","model":"STREAM Ultra X"})
    assert validate_offer(mission,_offer("Зарядная станция EcoFlow DELTA Lite Plus Stream Ultra")).verdict!=Verdict.PASS
    assert validate_offer(mission,_offer("Система энергообеспечения EcoFlow STREAM Ultra X")).verdict==Verdict.PASS


def test_prom_can_accept_same_product_when_model_is_omitted():
    mission=ProductMission(article="FAKE",source_data={"name":"Монітор Xiaomi Redmi A27Q 2025 2560x1440 2K IPS 100 Гц 27 Чорний","brand":"Xiaomi","model":"P27QCB-RA"})
    result=validate_offer(mission,_offer("Монітор 27 2K Xiaomi Redmi A27Q 2025 100 Гц IPS HDR10 чорний",Marketplace.PROM))
    assert result.verdict==Verdict.PASS
    assert any("model absent" in x for x in result.positive_evidence)


def test_prom_still_rejects_explicit_wrong_numeric_spec():
    mission=ProductMission(article="FAKE",source_data={"name":"Баллон газовый универсальный X-Treme 227 г 20 шт","brand":"X-Treme","model":"XT227"})
    result=validate_offer(mission,_offer("Баллон газовый универсальный X-Treme 220 г 20 шт",Marketplace.PROM))
    assert result.verdict!=Verdict.PASS
