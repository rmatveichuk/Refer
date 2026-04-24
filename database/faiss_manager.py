import os
import faiss
import numpy as np
from pathlib import Path
import config
import logging

logger = logging.getLogger(__name__)

class FaissManager:
    def __init__(self, index_path: Path, dimension: int = 768):
        self.index_path = index_path
        self.dimension = dimension
        self.index = self._load_or_create_index()

    def _load_or_create_index(self) -> faiss.IndexIDMap:
        """Loads index from disk or creates a new one."""
        if self.index_path.exists():
            try:
                index = faiss.read_index(str(self.index_path))
                logger.info(f"Loaded FAISS index with {index.ntotal} vectors.")
                return index
            except Exception as e:
                logger.error(f"Failed to load FAISS index: {e}")
        
        # Create a basic L2 distance index
        # We wrap it in IndexIDMap to assign arbitrary custom IDs (SQLite Asset IDs)
        quantizer = faiss.IndexFlatL2(self.dimension)
        index = faiss.IndexIDMap(quantizer)
        logger.info(f"Created new FAISS index (dimension={self.dimension}).")
        return index

    def save_index(self):
        """Saves current index state to disk."""
        faiss.write_index(self.index, str(self.index_path))

    def add_vector(self, asset_id: int, vector: np.ndarray):
        """Adds a single vector linked to an SQLite asset_id and saves index."""
        self.add_vector_no_save(asset_id, vector)
        self.save_index()

    def add_vectors_batch(self, asset_ids: list[int], vectors: np.ndarray):
        """Adds a batch of vectors linked to SQLite asset_ids without saving."""
        vectors = np.asarray(vectors, dtype=np.float32)
        if len(vectors.shape) == 1:
            vectors = np.expand_dims(vectors, axis=0)

        if vectors.shape[1] != self.dimension:
            raise ValueError(f"Expected dimension {self.dimension}, got {vectors.shape[1]}")
            
        if len(asset_ids) != vectors.shape[0]:
            raise ValueError(f"Mismatch: {len(asset_ids)} IDs for {vectors.shape[0]} vectors")

        id_array = np.array(asset_ids, dtype=np.int64)
        self.index.add_with_ids(vectors, id_array)

    def add_vector_no_save(self, asset_id: int, vector: np.ndarray):
        """Adds a single vector without saving. Use for batch operations."""
        self.add_vectors_batch([asset_id], vector)

    def search(self, query_vector: np.ndarray, k: int = 10) -> tuple[np.ndarray, np.ndarray]:
        """Searches for k nearest neighbors."""
        query_vector = np.asarray(query_vector, dtype=np.float32)
        if len(query_vector.shape) == 1:
            query_vector = np.expand_dims(query_vector, axis=0)
            
        distances, indices = self.index.search(query_vector, k)
        return distances[0], indices[0]

    def remove_ids(self, asset_ids: list[int]):
        """Removes vectors from index by their SQLite asset IDs."""
        if not asset_ids:
            return
            
        id_array = np.array(asset_ids, dtype=np.int64)
        # remove_ids returns number of removed vectors
        removed_count = self.index.remove_ids(id_array)
        if removed_count > 0:
            self.save_index()
            logger.info(f"Removed {removed_count} vectors from FAISS index.")
