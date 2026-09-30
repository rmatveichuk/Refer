"""
scrapers/studio_collector.py

Batch orchestrator and CLI for ingesting curated reference photography and renders
from the Top 100 Architecture Bureaus and Top 50 ArchViz Studios.

Usage:
    # 1. List registered studios:
    python -m scrapers.studio_collector --list

    # 2. Dry run preview for a single studio (no downloads or DB writes):
    python -m scrapers.studio_collector --studio "k-studio" --dry-run

    # 3. Live ingestion for a single studio (max 5 images per project, 2021-2026):
    python -m scrapers.studio_collector --studio "k-studio" --max-images-per-project 5

    # 4. Batch ingestion for top 3 architecture studios:
    python -m scrapers.studio_collector --type architecture --tier tier_1 --batch 3

    # 5. Check database stats for collected studios:
    python -m scrapers.studio_collector --stats
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from registry.registry_manager import StudioRegistry, StudioEntry
from scrapers.archdaily_parser import ArchDailyParser
from scrapers.manager import ScraperManager
from database.db_manager import DatabaseManager
import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("StudioCollector")


class StudioCollector:
    """Orchestrates ingesting references for studios in the registry."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.registry = StudioRegistry()
        self.db = db_manager or DatabaseManager(config.DB_PATH)

    def list_studios(
        self,
        studio_type: Optional[str] = None,
        tier: Optional[str] = None,
        country: Optional[str] = None,
        specialty: Optional[str] = None,
    ):
        """Displays formatted listing of registered studios."""
        studios = self.registry.filter(studio_type=studio_type, tier=tier, country=country, specialty=specialty)
        print(f"\n{'ID':<25} {'Name':<30} {'Type':<14} {'Tier':<8} {'Country':<14} {'Portfolio URL'}")
        print("-" * 125)
        for s in studios:
            url = s.archdaily_url or s.portfolio_url or s.website
            print(f"{s.id:<25} {s.name:<30} {s.type:<14} {s.tier:<8} {s.country:<14} {url[:40]}")
        print(f"\nTotal: {len(studios)} studios")

    def show_stats(self):
        """Displays database counts of ingested assets by studio/author."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    p.author,
                    COUNT(DISTINCT p.id) as project_count,
                    COUNT(a.id) as asset_count,
                    MIN(p.title) as sample_project
                FROM projects p
                LEFT JOIN assets a ON a.project_id = p.id
                GROUP BY p.author
                ORDER BY asset_count DESC
            """)
            rows = cur.fetchall()

            cur.execute("SELECT COUNT(*) FROM assets")
            total_assets = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM projects")
            total_projects = cur.fetchone()[0]

        print("\n" + "=" * 80)
        print(f" DATABASE REFERENCE INGESTION STATS (Total: {total_assets} assets across {total_projects} projects)")
        print("=" * 80)
        print(f"{'Author / Bureau':<35} {'Projects':<10} {'Images':<10} {'Sample Project'}")
        print("-" * 80)
        for r in rows:
            author = r["author"] or "Unknown"
            print(f"{author:<35} {r['project_count']:<10} {r['asset_count']:<10} {str(r['sample_project'])[:35]}")
        print("=" * 80 + "\n")

    def ingest_studio(
        self,
        studio: StudioEntry,
        dry_run: bool = False,
        max_images_per_project: int = 5,
        min_year: int = 2021,
        max_year: int = 2026,
    ) -> Dict[str, Any]:
        target_url = studio.archdaily_url or studio.portfolio_url
        logger.info(f"▶ Processing studio: '{studio.name}' ({studio.type}, {studio.country})")
        logger.info(f"  Target URL: {target_url}")

        if not target_url:
            logger.warning(f"Studio '{studio.name}' has no URL, skipping.")
            return {"status": "skipped", "reason": "no_url", "images": 0}

        collected_images = []
        is_archdaily = "archdaily.com" in target_url

        if is_archdaily:
            if dry_run:
                # Dry run preview callback: simply collect and display
                def on_image_preview(asset_data: Dict[str, Any]) -> bool:
                    collected_images.append(asset_data)
                    logger.info(
                        f"  [PREVIEW] Project: '{asset_data['project_title'][:40]}' "
                        f"({asset_data.get('year')}) | {asset_data['url'][:75]}"
                    )
                    return True

                parser = ArchDailyParser(
                    target_url,
                    on_image_found=on_image_preview,
                    min_year=min_year,
                    max_year=max_year,
                )
                parser.run()
                logger.info(f"✔ [DRY RUN] Would ingest {len(collected_images)} approved images for '{studio.name}'")
                return {"status": "dry_run", "images": len(collected_images)}
            else:
                # Live ingestion via ScraperManager (handles download, webp thumb, phash, DB)
                manager = ScraperManager(
                    ArchDailyParser,
                    target_url,
                    self.db,
                    category="architecture",
                    max_images_per_project=max_images_per_project,
                )
                
                # Tag callback to inject studio and TOP tags
                orig_process = manager.process_image_url
                def process_with_studio_tag(asset_data: Dict[str, Any]) -> bool:
                    tags = asset_data.setdefault("tags", [])
                    # Universal TOP tags for instant UI filtering
                    tags.extend(["топ", "top"])
                    # Studio identity
                    tags.append(studio.name)
                    tags.append(studio.id)
                    if studio.country:
                        tags.append(studio.country)
                    return orig_process(asset_data)

                manager.process_image_url = process_with_studio_tag
                manager.run()

                # Automatically embed newly ingested assets with SigLIP 2 into FAISS
                self.embed_unindexed_assets()

                return {"status": "success", "images": sum(manager._project_image_count.values())}

        else:
            logger.info(f"Non-ArchDaily studio: '{studio.name}' ({target_url}). Standard web crawling pending.")
            return {"status": "pending_adapter", "images": 0}

    def embed_unindexed_assets(self):
        """Indexes newly downloaded assets with SigLIP 2 vectors into FAISS immediately."""
        import sqlite3
        from database.faiss_manager import FaissManager
        from ai.engine import AiEngine

        unindexed_ids = self.db.get_unindexed_assets()
        if not unindexed_ids:
            return

        logger.info(f"Vectorizing {len(unindexed_ids)} newly collected assets with SigLIP 2 into FAISS...")
        engine = AiEngine()
        faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)

        batch_size = 32
        for i in range(0, len(unindexed_ids), batch_size):
            batch_ids = unindexed_ids[i : i + batch_size]
            with self.db.get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                q = f"SELECT id, thumbnail_path FROM assets WHERE id IN ({','.join('?' for _ in batch_ids)})"
                rows = cur.execute(q, batch_ids).fetchall()

            valid_ids = []
            paths = []
            for r in rows:
                if r["thumbnail_path"] and Path(r["thumbnail_path"]).exists():
                    valid_ids.append(r["id"])
                    paths.append(r["thumbnail_path"])

            if paths:
                vectors = engine.get_image_embeddings_batch(paths)
                for aid, vec in zip(valid_ids, vectors):
                    faiss_mgr.add_vector_no_save(aid, vec)
                    self.db.set_embedding_id(aid, aid)

        faiss_mgr.save_index()
        logger.info(f"✔ Successfully vectorized and indexed {len(unindexed_ids)} assets into FAISS.")

    def run_batch(
        self,
        count: int = 5,
        studio_type: str = "architecture",
        tier: str = "tier_1",
        dry_run: bool = False,
        max_images_per_project: int = 5,
        min_year: int = 2021,
        target_total: Optional[int] = None,
        offset: int = 0,
    ):
        """Runs batch ingestion for the top N studios starting from offset."""
        studios = self.registry.filter(studio_type=studio_type, tier=tier)
        selected = studios[offset : offset + count]
        logger.info(f"Starting batch run for {len(selected)} studios ({studio_type}, {tier}, offset={offset})...")

        results = []
        for i, s in enumerate(selected, 1):
            logger.info(f"\n--- [{i}/{len(selected)}] {s.name} ---")
            res = self.ingest_studio(
                s,
                dry_run=dry_run,
                max_images_per_project=max_images_per_project,
                min_year=min_year,
            )
            results.append((s.name, res))

            if target_total is not None:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT COUNT(DISTINCT at.asset_id)
                        FROM asset_tags at
                        JOIN tags t ON at.tag_id = t.id
                        WHERE t.name = 'топ'
                    """)
                    row = cur.fetchone()
                    current_total = row[0] if row else 0
                logger.info(f"Progress towards target: {current_total} / {target_total} images with tag 'топ'")
                if current_total >= target_total:
                    logger.info(f"🎯 Target of {target_total} top references reached! Current total: {current_total}")
                    break

            # Ethical politeness pause between studios
            time.sleep(3)

        print("\n" + "=" * 60)
        print(" BATCH INGESTION SUMMARY")
        print("=" * 60)
        for name, res in results:
            print(f" {name:<30} -> {res['status']} ({res.get('images', 0)} images)")
        print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Refer Reference Studio Ingestion Orchestrator")
    parser.add_argument("--list", action="store_true", help="List registered studios")
    parser.add_argument("--stats", action="store_true", help="Show database collection stats")
    parser.add_argument("--studio", type=str, help="Studio ID or Name to ingest")
    parser.add_argument("--studios", type=str, help="Comma-separated studio IDs or names to ingest")
    parser.add_argument("--batch", type=int, help="Number of studios to process in batch")
    parser.add_argument("--offset", type=int, default=0, help="Offset to start batch processing from in the registry (default: 0)")
    parser.add_argument("--type", type=str, default="architecture", choices=["architecture", "archviz"], help="Studio type")
    parser.add_argument("--tier", type=str, default="tier_1", choices=["tier_1", "tier_2", "tier_3"], help="Studio tier")
    parser.add_argument("--country", type=str, help="Filter by country")
    parser.add_argument("--specialty", type=str, help="Filter by specialty (e.g. mediterranean_villas, brutalist_concrete)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without downloading or database writes")
    parser.add_argument("--max-images-per-project", type=int, default=5, help="Max curated images per project (default: 5)")
    parser.add_argument("--min-year", type=int, default=2021, help="Minimum project completion year (default: 2021)")
    parser.add_argument("--target-total", type=int, help="Stop batch ingestion when this total count of 'топ' images is reached in DB")

    args = parser.parse_args()
    collector = StudioCollector()

    if args.list:
        collector.list_studios(
            studio_type=args.type if "--type" in sys.argv else None,
            tier=args.tier if "--tier" in sys.argv else None,
            country=args.country,
            specialty=args.specialty,
        )
    elif args.stats:
        collector.show_stats()
    elif args.studios:
        studio_ids = [x.strip().lower() for x in args.studios.split(",") if x.strip()]
        selected = []
        for sid in studio_ids:
            s = collector.registry.get_by_id(sid)
            if not s:
                matches = [x for x in collector.registry.studios if sid in x.name.lower() or sid in x.id.lower()]
                if matches:
                    s = matches[0]
            if s and s not in selected:
                selected.append(s)
            elif not s:
                logger.warning(f"Studio '{sid}' not found in registry.")

        results = []
        for i, s in enumerate(selected, 1):
            logger.info(f"\n--- [{i}/{len(selected)}] {s.name} ---")
            res = collector.ingest_studio(
                s,
                dry_run=args.dry_run,
                max_images_per_project=args.max_images_per_project,
                min_year=args.min_year,
            )
            results.append((s.name, res))
            time.sleep(2)

        print("\n" + "=" * 60)
        print(" BATCH INGESTION SUMMARY")
        print("=" * 60)
        for name, res in results:
            print(f" {name:<30} -> {res['status']} ({res.get('images', 0)} images)")
        print("=" * 60 + "\n")
    elif args.studio:
        # Search by id or partial name
        s = collector.registry.get_by_id(args.studio.lower())
        if not s:
            matches = [x for x in collector.registry.studios if args.studio.lower() in x.name.lower()]
            if matches:
                s = matches[0]

        if not s:
            print(f"Error: Studio '{args.studio}' not found in registry.")
            sys.exit(1)

        collector.ingest_studio(
            s,
            dry_run=args.dry_run,
            max_images_per_project=args.max_images_per_project,
            min_year=args.min_year,
        )
    elif args.batch:
        collector.run_batch(
            count=args.batch,
            studio_type=args.type,
            tier=args.tier,
            dry_run=args.dry_run,
            max_images_per_project=args.max_images_per_project,
            min_year=args.min_year,
            target_total=args.target_total,
            offset=args.offset,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
