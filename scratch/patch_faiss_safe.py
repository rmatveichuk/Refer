import sys

with open('database/faiss_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

# Replace IDSelectorBatch with IDSelectorArray and numpy array
new_sel = """
            if not valid_ids:
                return np.array([]), np.array([])
                
            # Convert to numpy array and sort it (IDSelectorArray requires sorted array)
            import numpy as np
            valid_ids_arr = np.array(valid_ids, dtype=np.int64)
            valid_ids_arr.sort()
            
            sel = faiss.IDSelectorArray(valid_ids_arr)
            params = faiss.SearchParameters(sel=sel)
"""

content = re.sub(
    r'if not valid_ids:\s*# If valid_ids is empty list.*?sel = faiss\.IDSelectorBatch\(valid_ids\)\s*params = faiss\.SearchParameters\(sel=sel\)',
    new_sel.strip(),
    content,
    flags=re.DOTALL
)

with open('database/faiss_manager.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched faiss_manager.py to use IDSelectorArray")