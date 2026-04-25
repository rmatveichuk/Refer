import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

patch = """
    def search(self, query_vector: np.ndarray, k: int = 10, valid_ids: list[int] = None) -> tuple[np.ndarray, np.ndarray]:
        \"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs.\"\"\"
        if query_vector is None:
            query_vector = np.array([])
            
        try:
            query_vector = np.asarray(query_vector, dtype=np.float32)
        except Exception as e:
            import logging
            logging.error(f"Error converting query_vector to float32: {e}, type: {type(query_vector)}")
            query_vector = np.array([], dtype=np.float32)
"""

import re
content = re.sub(
    r'def search\(self, query_vector: np\.ndarray, k: int = 10, valid_ids: list\[int\] = None\) -> tuple\[np\.ndarray, np\.ndarray\]:\n\s*\"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs\.\"\"\"\n\s*query_vector = np\.asarray\(query_vector, dtype=np\.float32\)',
    patch.strip(),
    content,
    flags=re.DOTALL
)

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched faiss_manager.py search safety")