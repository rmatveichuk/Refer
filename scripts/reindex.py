import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.absolute()))

import logging
import sqlite3
import numpy as np
from ai.engine import AiEngine
from database.db_manager import DatabaseManager
from database.faiss_manager import FaissManager
import config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def reindex_all():
    logger.info("🚀 Starting full re-indexing and migration to SigLIP-so400m...")

    # 1. Initialize DB and Engine
    db = DatabaseManager(config.DB_PATH)
    engine = AiEngine()
    
    # 2. Prepare FAISS (Delete old index)
    if config.FAISS_PATH.exists():
        logger.info(f"Removing old index: {config.FAISS_PATH}")
        config.FAISS_PATH.unlink()
    
    faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)

    # 3. Fetch all assets from SQLite
    with db.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, thumbnail_path FROM assets WHERE thumbnail_path IS NOT NULL AND thumbnail_path != ''")
        assets = cur.fetchall()

    total = len(assets)
    logger.info(f"Found {total} assets to re-index.")

    # 4. Processing loop
    success_count = 0
    for i, row in enumerate(assets):
        asset_id = row['id']
        thumb_path = row['thumbnail_path']
        
        if not Path(thumb_path).exists():
            logger.warning(f"[{i+1}/{total}] Skipping asset #{asset_id}: File not found at {thumb_path}")
            continue

        try:
            # Generate embedding
            vector = engine.get_image_embedding(thumb_path)
            
            # Add to FAISS (using asset_id as ID)
            faiss_mgr.add_vector_no_save(asset_id, vector)
            
            # Update SQLite
            db.set_embedding_id(asset_id, asset_id)
            
            success_count += 1
            if (i + 1) % 50 == 0:
                logger.info(f"Processed {i+1}/{total} assets...")
                faiss_mgr.save_index() # Periodic save
                
        except Exception as e:
            logger.error(f"Failed to process asset #{asset_id}: {e}")

    # Final save
    faiss_mgr.save_index()
    logger.info(f"✅ Re-indexing complete! Successfully indexed {success_count}/{total} assets.")
    logger.info(f"New index saved to: {config.FAISS_PATH}")

if __name__ == "__main__":
    reindex_all()
