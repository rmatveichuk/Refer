import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_search_start = """
    def search(self, query_vector: np.ndarray, k: int = 10, valid_ids: list[int] = None) -> tuple[np.ndarray, np.ndarray]:
        \"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs.\"\"\"
        query_vector = np.asarray(query_vector, dtype=np.float32)
        
        # Prevent FAISS crash if vector contains NaNs
        if np.isnan(query_vector).any():
            query_vector = np.nan_to_num(query_vector)
            
        if len(query_vector.shape) == 1:
            query_vector = np.expand_dims(query_vector, axis=0)
"""

import re
content = re.sub(
    r'def search\(self, query_vector: np\.ndarray, k: int = 10, valid_ids: list\[int\] = None\) -> tuple\[np\.ndarray, np\.ndarray\]:.*?if len\(query_vector\.shape\) == 1:',
    new_search_start.strip() + "\n        if len(query_vector.shape) == 1:",
    content,
    flags=re.DOTALL
)

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched FAISS to prevent NaN crash")