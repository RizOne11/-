from __future__ import annotations

from abc import ABC, abstractmethod

from puma_scouts.models import Marketplace, Offer, ProductMission, ScanReport


class MarketplaceScout(ABC):
    marketplace: Marketplace

    @abstractmethod
    async def generate_queries(self, mission: ProductMission) -> list[str]:
        """Build several marketplace search queries from all useful source fields."""

    @abstractmethod
    async def discover(self, mission: ProductMission, query: str) -> list[Offer]:
        """Collect broad candidates. Recall is preferred over premature rejection."""

    @abstractmethod
    async def scan(self, mission: ProductMission) -> ScanReport:
        """Run discovery, deduplication and preliminary validation for one mission."""
