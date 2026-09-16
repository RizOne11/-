from puma_scouts.models import Marketplace, Offer, ProductMission, Verdict
from puma_scouts.validator import validate_offer


def _mission():
    return ProductMission(
        article="CONTROL-NOT-A-SEARCH-KEY",
        source_data={"name": "Монітор Redmi A27Q 2025", "brand": "Redmi", "model": "P27QCB-RA"},
    )


def test_exact_model_passes():
    offer = Offer(
        article="CONTROL-NOT-A-SEARCH-KEY",
        marketplace=Marketplace.EPICENTR,
        title="Монітор Redmi A27Q 2025 P27QCB-RA 27 дюймів",
        attributes={"model": "P27QCB-RA", "brand": "Redmi"},
        url="https://epicentrk.ua/ua/shop-mplc/exact.html",
    )
    result = validate_offer(_mission(), offer)
    assert result.verdict == Verdict.PASS
    assert any("P27QCB-RA" in evidence for evidence in result.positive_evidence)


def test_family_name_does_not_pass_wrong_generation():
    offer = Offer(
        article="CONTROL-NOT-A-SEARCH-KEY",
        marketplace=Marketplace.EPICENTR,
        title="Монітор Redmi A27Q 2026 27 дюймів",
        attributes={"model": "A27Q", "brand": "Redmi"},
        url="https://epicentrk.ua/ua/shop-mplc/wrong-generation.html",
    )
    result = validate_offer(_mission(), offer)
    assert result.verdict != Verdict.PASS
    assert any("expected model not confirmed" in conflict for conflict in result.conflicts)


def test_bundle_with_family_name_does_not_pass():
    offer = Offer(
        article="CONTROL-NOT-A-SEARCH-KEY",
        marketplace=Marketplace.EPICENTR,
        title="Комплект ПК Ryzen 5 + монітор Redmi A27Q",
        attributes={"brand": "Redmi"},
        url="https://epicentrk.ua/ua/shop-mplc/bundle.html",
    )
    result = validate_offer(_mission(), offer)
    assert result.verdict != Verdict.PASS
