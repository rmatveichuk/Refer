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
        
        try:
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
                
                # Generate embedding
                vector = self.ai.get_image_embedding(thumb_path)
                
                # Add to FAISS
                self.faiss_mgr.add_vector_no_save(asset_id, vector)
                
                # Update DB
                self.db.set_embedding_id(asset_id, asset_id)
                
                indexed_count += 1
                self.signals.progress.emit(i + 1, total, f"Indexing #{asset_id}")
                
                if (i + 1) % 20 == 0:
                    self.faiss_mgr.save_index()

            self.faiss_mgr.save_index()
            self.signals.finished.emit(indexed_count)
            
        except Exception as e:
            logger.error(f"Indexing worker failed: {e}")
            self.signals.error.emit(str(e))
