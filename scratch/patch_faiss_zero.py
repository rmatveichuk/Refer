import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

patch = """
        if valid_ids is not None:
            if not valid_ids:
                return np.array([]), np.array([])
                
            # If the vector is empty, FAISS will crash if we run search, even with valid_ids.
            # We should just return the valid_ids directly.
            if query_vector.shape[1] == 0:
                k_ret = min(k, len(valid_ids))
                return np.zeros(k_ret, dtype=np.float32), np.array(valid_ids[:k_ret], dtype=np.int64)

            import numpy as np
            valid_ids_arr = np.array(valid_ids, dtype=np.int64)
            valid_ids_arr.sort()
            
            sel = faiss.IDSelectorArray(valid_ids_arr)
            params = faiss.SearchParameters(sel=sel)
            try:
                distances, indices = self.index.search(query_vector, k, params=params)
            except Exception as e:
                import logging
                logging.error(f"FAISS search error with valid_ids: {e}")
                return np.array([]), np.array([])
        else:
            if query_vector.shape[1] == 0:
                return np.array([]), np.array([])
                
            try:
                distances, indices = self.index.search(query_vector, k)
            except Exception as e:
                import logging
                logging.error(f"FAISS search error: {e}")
                return np.array([]), np.array([])
"""

import re
content = re.sub(
    r'if valid_ids is not None:\s*# We must use faiss.*?distances, indices = self\.index\.search\(query_vector, k\)',
    patch.strip(),
    content,
    flags=re.DOTALL
)

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched FAISS to safely handle zero-length vectors")