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

with db.get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT id FROM assets WHERE source_id=3 LIMIT 1")
    ext_id = cur.fetchone()[0]
    
    internal_id = -1
    for i in range(index.ntotal):
        if index.id_map.at(i) == ext_id:
            internal_id = i
            break

    if internal_id != -1:
        v = base_index.reconstruct(internal_id)
        v = np.expand_dims(v, axis=0)
        
        distances, ids = faiss_mgr.search(v, k=20)
        
        print("Search with ArchDaily Image (ID", ext_id, "):")
        ids_list = [int(x) for x in ids]
        placeholders = ','.join('?' for _ in ids_list)
        cur.execute(f"SELECT a.id, s.domain FROM assets a LEFT JOIN sources s ON a.source_id = s.id WHERE a.id IN ({placeholders})", ids_list)
        domain_map = {row[0]: row[1] for row in cur.fetchall()}
        
        for d, i in zip(distances, ids):
            print(f"Dist: {d:.4f}, ID: {i}, Domain: {domain_map.get(int(i), 'Unknown')}")
