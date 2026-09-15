from decimal import Decimal

from puma_scouts.models import Marketplace, Offer, ProductMission
from puma_scouts.pivots import DiscoveryState, PivotKind, next_pivot_queries, pivots_from_offer


def _mission() -> ProductMission:
    return ProductMission(
        article="supplier-001",
        source_data={
            "brand": "Xiaomi",
            "name": "Xiaomi Redmi Monitor A27Q 2025",
            "mpn": "P27QCB-RA",
        },
    )


def test_offer_can_open_new_identifier_and_seller_graphs() -> None:
    offer = Offer(
        article="supplier-001",
        marketplace=Marketplace.ROZETKA,
        marketplace_product_id="592071073",
        seller_name="WOWtech",
        title="Xiaomi Redmi Monitor A27Q 2025 P27QCB-RA",
        price=Decimal("8999"),
        url="https://hard.rozetka.com.ua/ua/592071073/p592071073/",
        attributes={"gtin": "6941812791901", "model": "P27QCB-RA"},
    )

    pivots = pivots_from_offer(_mission(), offer)
    values = {(p.kind, p.value) for p in pivots}

    assert (PivotKind.IDENTIFIER, "6941812791901") in values
    assert (PivotKind.SELLER, "WOWtech") in values
    assert (PivotKind.MARKETPLACE_CARD, "592071073") in values


def test_next_round_changes_search_graph_instead_of_repeating_original_query() -> None:
    offer = Offer(
        article="supplier-001",
        marketplace=Marketplace.ROZETKA,
        marketplace_product_id="592071073",
        seller_name="WOWtech",
        title="Xiaomi Redmi Monitor A27Q 2025 P27QCB-RA",
        price=Decimal("8999"),
        url="https://hard.rozetka.com.ua/ua/592071073/p592071073/",
        attributes={"gtin": "6941812791901"},
    )
    state = DiscoveryState()
    state.add_query("Xiaomi P27QCB-RA")

    queries = next_pivot_queries(_mission(), [offer], state)

    assert any("6941812791901" in q for q in queries)
    assert any("592071073" in q for q in queries)
    assert all(q.casefold() != "xiaomi p27qcb-ra" for q in queries)


def test_same_pivot_is_not_spent_twice() -> None:
    offer = Offer(
        article="supplier-001",
        marketplace=Marketplace.PROM,
        seller_name="Seller One",
        title="Xiaomi P27QCB-RA 6941812791901",
        url="https://example.com/product",
    )
    state = DiscoveryState()

    first = next_pivot_queries(_mission(), [offer], state)
    second = next_pivot_queries(_mission(), [offer], state)

    assert first
    assert second == []
