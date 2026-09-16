import pytest

from puma_scouts.models import Marketplace, ProductMission
from puma_scouts.scouts.catalog import AlloScout, ComfyScout, FoxtrotScout, HotlineScout, KastaScout


@pytest.mark.asyncio
@pytest.mark.parametrize("scout_cls, marketplace", [
    (AlloScout, Marketplace.ALLO),
    (FoxtrotScout, Marketplace.FOXTROT),
    (ComfyScout, Marketplace.COMFY),
    (KastaScout, Marketplace.KASTA),
    (HotlineScout, Marketplace.HOTLINE),
])
async def test_rollout_scouts_never_search_source_article(scout_cls, marketplace):
    scout = scout_cls()
    mission = ProductMission(article="SOURCE-ARTICLE-002288", source_data={
        "name": "Монитор Xiaomi Redmi A27Q 2025 P27QCB-RA 2560x1440 2K IPS 100 Гц 27",
        "brand": "Xiaomi",
        "model": "P27QCB-RA",
    })
    queries = await scout.generate_queries(mission)
    assert scout.marketplace == marketplace
    assert queries
    assert all("source-article-002288" not in q.casefold() for q in queries)
    assert any("p27qcb-ra" in q.casefold() for q in queries)
