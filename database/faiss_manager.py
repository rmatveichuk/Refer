import os
import sys
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

    def reset_index(self):
        """Clears the FAISS index and saves an empty index to disk."""
        quantizer = faiss.IndexFlatL2(self.dimension)
        self.index = faiss.IndexIDMap(quantizer)
        self.save_index()
        logger.info(f"Reset FAISS index (dimension={self.dimension}).")

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

        if not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) <= 1e-12):
            raise ValueError("Cannot index non-finite or zero embeddings")

        id_array = np.array(asset_ids, dtype=np.int64)
        self.index.add_with_ids(vectors, id_array)

    def add_vector_no_save(self, asset_id: int, vector: np.ndarray):
        """Adds a single vector without saving. Use for batch operations."""
        self.add_vectors_batch([asset_id], vector)

    def search(self, query_vector: np.ndarray, k: int = 10, valid_ids: list[int] = None) -> tuple[np.ndarray, np.ndarray]:
        """Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs."""
        if query_vector is None:
            query_vector = np.array([])
            
        try:
            query_vector = np.asarray(query_vector, dtype=np.float32)
        except Exception as e:
            raise ValueError("Search embedding must contain numeric values") from e
        
        if not np.isfinite(query_vector).all():
            raise ValueError("Search embedding contains non-finite values")
            
        if len(query_vector.shape) == 1:
            query_vector = np.expand_dims(query_vector, axis=0)

        if query_vector.ndim != 2 or query_vector.shape[0] != 1:
            raise ValueError("Expected one search embedding")
        if query_vector.shape[1] and (query_vector.shape[1] != self.dimension or np.linalg.norm(query_vector) <= 1e-12):
            raise ValueError("Search embedding has invalid dimension or zero length")
        if k <= 0:
            return np.array([]), np.array([])

        if sys.platform == 'win32' and query_vector.shape[1] != 0:
            # The Windows FAISS and PyTorch wheels ship incompatible OpenMP runtimes.
            # Query the existing flat index through a NumPy view, without calling
            # FAISS's OpenMP search or disabling its duplicate-runtime protection.
            return self._search_flat_without_openmp(query_vector[0], k, valid_ids)
            
        if valid_ids is not None:
            if not valid_ids:
                return np.array([]), np.array([])
                
            # If the vector is empty, FAISS will crash if we run search, even with valid_ids.
            # We should just return the valid_ids directly.
            if query_vector.shape[1] == 0:
                k_ret = min(k, len(valid_ids))
                return np.zeros(k_ret, dtype=np.float32), np.array(valid_ids[:k_ret], dtype=np.int64)

            valid_ids_arr = np.array(valid_ids, dtype=np.int64)
            valid_ids_arr.sort()
            
            sel = faiss.IDSelectorArray(valid_ids_arr)
            params = faiss.SearchParameters(sel=sel)
            try:
                distances, indices = self.index.search(query_vector, k, params=params)
            except Exception as e:
                logger.error(f"FAISS search error with valid_ids: {e}")
                raise RuntimeError("Не удалось выполнить поиск в выбранных источниках.") from e
        else:
            if query_vector.shape[1] == 0:
                return np.array([]), np.array([])
                
            try:
                distances, indices = self.index.search(query_vector, k)
            except Exception as e:
                logger.error(f"FAISS search error: {e}")
                raise RuntimeError("Не удалось выполнить поиск в индексе.") from e
            
        return distances[0], indices[0]

    def _search_flat_without_openmp(self, query, k, valid_ids):
        flat = faiss.downcast_index(self.index.index)
        if not isinstance(flat, faiss.IndexFlatL2):
            raise RuntimeError('Безопасный поиск Windows требует индекс IndexFlatL2.')
        ids = self.get_all_ids()
        positions = np.arange(len(ids)) if valid_ids is None else np.flatnonzero(np.isin(ids, valid_ids))
        vectors = faiss.rev_swig_ptr(flat.get_xb(), flat.ntotal * flat.d).reshape(flat.ntotal, flat.d)
        distances = np.empty(len(positions), dtype=np.float32)
        # Bound scratch memory instead of copying the entire 1152-dimensional library.
        for start in range(0, len(positions), 1024):
            batch = positions[start:start + 1024]
            delta = vectors[batch] - query
            distances[start:start + len(batch)] = np.einsum('ij,ij->i', delta, delta)
        order = np.lexsort((ids[positions], distances))[:k]
        found_distances = np.full(k, np.inf, dtype=np.float32)
        found_ids = np.full(k, -1, dtype=np.int64)
        found_distances[:len(order)] = distances[order]
        found_ids[:len(order)] = ids[positions[order]]
        return found_distances, found_ids

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

    def get_all_ids(self) -> np.ndarray:
        """Returns all asset IDs currently stored in the FAISS index."""
        return faiss.vector_to_array(self.index.id_map)
