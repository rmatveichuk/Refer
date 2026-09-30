import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).parent / "studios.json"

@dataclass
class StudioEntry:
    id: str
    name: str
    type: str  # "architecture" | "archviz"
    tier: str  # "tier_1" | "tier_2" | "tier_3"
    country: str
    website: str
    portfolio_url: str
    archdaily_url: str = ""
    specialties: List[str] = field(default_factory=list)
    signature_style: str = ""

class StudioRegistry:
    """Управление реестром эталонных студий архитектуры и 3D-визуализации."""

    def __init__(self, json_path: Optional[Path] = None):
        self.path = json_path or REGISTRY_FILE
        self.studios: List[StudioEntry] = []
        self.load()

    def load(self):
        """Загружает список студий из JSON-файла."""
        if not self.path.exists():
            logger.warning(f"Registry file not found at {self.path}")
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_studios = data.get("studios", [])
                self.studios = [
                    StudioEntry(
                        id=s["id"],
                        name=s["name"],
                        type=s["type"],
                        tier=s.get("tier", "tier_1"),
                        country=s.get("country", ""),
                        website=s.get("website", ""),
                        portfolio_url=s.get("portfolio_url", ""),
                        archdaily_url=s.get("archdaily_url", ""),
                        specialties=s.get("specialties", []),
                        signature_style=s.get("signature_style", "")
                    )
                    for s in raw_studios
                ]
            logger.info(f"Loaded {len(self.studios)} studios from {self.path.name}")
        except Exception as e:
            logger.error(f"Failed to load studio registry: {e}")

    def get_by_id(self, studio_id: str) -> Optional[StudioEntry]:
        """Возвращает студию по её идентификатору."""
        for s in self.studios:
            if s.id == studio_id:
                return s
        return None

    def filter(self, 
               studio_type: Optional[str] = None, 
               tier: Optional[str] = None, 
               specialty: Optional[str] = None,
               country: Optional[str] = None) -> List[StudioEntry]:
        """Фильтрует студии по заданным критериям."""
        results = self.studios
        if studio_type:
            results = [s for s in results if s.type == studio_type]
        if tier:
            results = [s for s in results if s.tier == tier]
        if specialty:
            results = [s for s in results if specialty in s.specialties]
        if country:
            results = [s for s in results if s.country.lower() == country.lower()]
        return results

    def get_portfolio_urls(self, studio_type: Optional[str] = None, tier: str = "tier_1") -> List[Dict[str, str]]:
        """Возвращает список URL портфолио для скрапера."""
        filtered = self.filter(studio_type=studio_type, tier=tier)
        return [{"id": s.id, "name": s.name, "url": s.portfolio_url} for s in filtered if s.portfolio_url]

    def add_studio(self, studio: StudioEntry, save_after: bool = True):
        """Добавляет новую студию в реестр."""
        if any(s.id == studio.id for s in self.studios):
            logger.warning(f"Studio with id '{studio.id}' already exists.")
            return

        self.studios.append(studio)
        if save_after:
            self.save()

    def save(self):
        """Сохраняет реестр обратно в JSON."""
        data = {
            "version": "1.0.0",
            "updated_at": "2026-09-30",
            "studios": [
                {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "tier": s.tier,
                    "country": s.country,
                    "website": s.website,
                    "portfolio_url": s.portfolio_url,
                    "specialties": s.specialties,
                    "signature_style": s.signature_style
                }
                for s in self.studios
            ]
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(self.studios)} studios to {self.path}")

if __name__ == "__main__":
    registry = StudioRegistry()
    print(f"Total studios: {len(registry.studios)}")
    arch = registry.filter(studio_type="architecture")
    viz = registry.filter(studio_type="archviz")
    print(f"Architecture studios: {len(arch)}")
    print(f"ArchViz studios: {len(viz)}")
    villas = registry.filter(specialty="mediterranean_villas")
    print(f"Mediterranean villa specialists: {[s.name for s in villas]}")
