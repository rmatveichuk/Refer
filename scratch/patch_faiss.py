import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_search = """
    def search(self, query_vector: np.ndarray, k: int = 10, valid_ids: list[int] = None) -> tuple[np.ndarray, np.ndarray]:
        \"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs.\"\"\"
        query_vector = np.asarray(query_vector, dtype=np.float32)
        if len(query_vector.shape) == 1:
            query_vector = np.expand_dims(query_vector, axis=0)
            
        if valid_ids is not None:
            # We must use faiss.IDSelectorBatch for pre-filtering
            # It requires an array of IDs
            if not valid_ids:
                # If valid_ids is empty list but not None, return empty results
                return np.array([]), np.array([])
                
            sel = faiss.IDSelectorBatch(valid_ids)
            params = faiss.SearchParameters(sel=sel)
            distances, indices = self.index.search(query_vector, k, params=params)
        else:
            distances, indices = self.index.search(query_vector, k)
            
        return distances[0], indices[0]
"""

# Replace the search method
import re
# Find the start of def search and the start of def remove_ids
start_idx = content.find("    def search(")
end_idx = content.find("    def remove_ids(")
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_search.strip() + "\n\n" + content[end_idx:]

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched faiss_manager.py")
