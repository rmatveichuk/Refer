import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

# Remove the inner `import numpy as np` from the except block if it exists
# Or just ensure the file starts with `import numpy as np`
patch = """
    def search(self, query_vector: np.ndarray, k: int = 10, valid_ids: list[int] = None) -> tuple[np.ndarray, np.ndarray]:
        \"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs.\"\"\"
        import numpy as np  # Explicitly import np here just to be 100% safe against UnboundLocalError
        
        if query_vector is None:
            query_vector = np.array([])
            
        try:
            query_vector = np.asarray(query_vector, dtype=np.float32)
        except Exception as e:
            import logging
            logging.error(f"Error converting query_vector to float32: {e}, type: {type(query_vector)}")
            query_vector = np.array([], dtype=np.float32)
"""

content = re.sub(
    r'def search\(self, query_vector: np\.ndarray, k: int = 10, valid_ids: list\[int\] = None\) -> tuple\[np\.ndarray, np\.ndarray\]:\n\s*\"\"\"Searches for k nearest neighbors, optionally filtering by valid SQLite asset IDs\.\"\"\"\n\s*if query_vector is None:\n\s*query_vector = np\.array\(\[\]\)\n\s*try:\n\s*query_vector = np\.asarray\(query_vector, dtype=np\.float32\)\n\s*except Exception as e:\n\s*import logging\n\s*logging\.error\(f"Error converting query_vector to float32: \{e\}, type: \{type\(query_vector\)\}"\)\n\s*query_vector = np\.array\(\[\], dtype=np\.float32\)',
    patch.strip(),
    content,
    flags=re.DOTALL
)

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched numpy import in faiss_manager.py search")