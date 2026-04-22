import logging
import numpy as np
from PyQt6.QtCore import QRunnable, pyqtSignal, QObject
from ai.engine import AiEngine
from database.db_manager import DatabaseManager
from database.faiss_manager import FaissManager
from pathlib import Path

logger = logging.getLogger(__name__)

class IndexSignals(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

class SigLipIndexWorker(QRunnable):
    def __init__(self, db: DatabaseManager, faiss_mgr: FaissManager, ai_engine: AiEngine, asset_ids: list):
        super().__init__()
        self.db = db
        self.faiss_mgr = faiss_mgr
        self.ai = ai_engine
        self.asset_ids = asset_ids
        self.signals = IndexSignals()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        total = len(self.asset_ids)
        indexed_count = 0
        batch_size = 64  # Увеличиваем батч для RTX 4060
        bulk_fetch_size = 512 # Подгружаем пути сразу для 512 ассетов
        
        try:
            for start_idx in range(0, total, bulk_fetch_size):
                if self._is_cancelled:
                    break
                
                end_idx = min(start_idx + bulk_fetch_size, total)
                chunk_ids = self.asset_ids[start_idx:end_idx]
                
                # Fetch thumbnail paths in one query for the chunk
                paths_map = {}
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    placeholders = ",".join(["?"] * len(chunk_ids))
                    cur.execute(f"SELECT id, thumbnail_path FROM assets WHERE id IN ({placeholders})", chunk_ids)
                    for row in cur.fetchall():
                        if row['thumbnail_path'] and Path(row['thumbnail_path']).exists():
                            paths_map[row['id']] = row['thumbnail_path']
                
                # Process the chunk in smaller batches for AI inference
                chunk_ids_with_paths = [aid for aid in chunk_ids if aid in paths_map]
                
                for i in range(0, len(chunk_ids_with_paths), batch_size):
                    if self._is_cancelled:
                        break
                        
                    batch_ids = chunk_ids_with_paths[i:i + batch_size]
                    batch_paths = [paths_map[aid] for aid in batch_ids]
                    
                    # Generate embeddings batch
                    vectors = self.ai.get_image_embeddings_batch(batch_paths)
                    
                    # Add to FAISS
                    self.faiss_mgr.add_vectors_batch(batch_ids, vectors)
                    
                    # Update DB
                    self.db.set_embedding_ids_batch(batch_ids)
                    
                    indexed_count += len(batch_ids)
                    self.signals.progress.emit(start_idx + i + len(batch_ids), total, f"Indexing batch ({indexed_count}/{total})")
                    
                    # Сохраняем индекс каждые 512 векторов, чтобы не потерять прогресс
                    if indexed_count % 512 == 0:
                        self.faiss_mgr.save_index()

            self.faiss_mgr.save_index()
            self.signals.finished.emit(indexed_count)
            
        except Exception as e:
            logger.error(f"Indexing worker failed: {e}")
            self.signals.error.emit(str(e))
