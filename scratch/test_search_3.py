import faiss
import sqlite3
import config
import numpy as np
from database.faiss_manager import FaissManager
from database.db_manager import DatabaseManager

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH)

index = faiss_mgr.index
base_index = faiss.downcast_index(index.index)

print("Checking norms for ArchDaily images...")
count_ad = 0
count_behance = 0
for i in range(min(5000, index.ntotal)):
    internal_id = i
    ext_id = index.id_map.at(internal_id)
    vec = base_index.reconstruct(internal_id)
    norm = np.linalg.norm(vec)
    
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT source_id FROM assets WHERE id=?", (int(ext_id),))
        row = cur.fetchone()
        src = row[0] if row else -1
        
    if src == 3 and count_ad < 5:
        print(f"ArchDaily Ext {ext_id}: norm = {norm:.4f}")
        count_ad += 1
    elif src == 2 and count_behance < 5:
        # print(f"Behance Ext {ext_id}: norm = {norm:.4f}")
        count_behance += 1
        
    if count_ad == 5 and count_behance == 5:
        break
