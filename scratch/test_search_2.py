import faiss
import sqlite3
import config
import numpy as np
from database.faiss_manager import FaissManager
from database.db_manager import DatabaseManager

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH)

index = faiss_mgr.index

print("Total vectors:", index.ntotal)

# IndexIDMap wraps another index
base_index = faiss.downcast_index(index.index)
print("Base index type:", type(base_index))

# reconstruct a few vectors
try:
    for i in range(10):
        internal_id = i
        vec = base_index.reconstruct(internal_id)
        ext_id = index.id_map.at(internal_id)
        norm = np.linalg.norm(vec)
        
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT source_id FROM assets WHERE id=?", (int(ext_id),))
            row = cur.fetchone()
            src = row[0] if row else "Unknown"
        
        print(f"Internal {internal_id} -> Ext {ext_id} (Source {src}): norm = {norm:.4f}")
except Exception as e:
    print("Error:", e)
