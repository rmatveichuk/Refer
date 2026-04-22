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
        batch_size = 32 # Увеличиваем размер батча для RTX 4060, так как память разгружена
        
        try:
            current_batch_paths = []
            current_batch_ids = []
            
            for i, asset_id in enumerate(self.asset_ids):
                if self._is_cancelled:
                    break
                
                # Fetch thumbnail path
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT thumbnail_path FROM assets WHERE id = ?", (asset_id,))
                    row = cur.fetchone()
                
                if not row or not row['thumbnail_path']:
                    continue
                
                thumb_path = row['thumbnail_path']
                if not Path(thumb_path).exists():
                    continue
                
                current_batch_paths.append(thumb_path)
                current_batch_ids.append(asset_id)
                
                if len(current_batch_paths) >= batch_size or i == total - 1:
                    # Generate embeddings batch
                    vectors = self.ai.get_image_embeddings_batch(current_batch_paths)
                    
                    # Add to FAISS
                    self.faiss_mgr.add_vectors_batch(current_batch_ids, vectors)
                    
                    # Update DB
                    self.db.set_embedding_ids_batch(current_batch_ids)
                    
                    indexed_count += len(current_batch_ids)
                    self.signals.progress.emit(i + 1, total, f"Indexing #{asset_id} (batch)")
                    
                    current_batch_paths = []
                    current_batch_ids = []
                    
                    if indexed_count % (batch_size * 5) == 0:
                        self.faiss_mgr.save_index()

            self.faiss_mgr.save_index()
            self.signals.finished.emit(indexed_count)
            
        except Exception as e:
            logger.error(f"Indexing worker failed: {e}")
            self.signals.error.emit(str(e))
