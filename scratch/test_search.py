import faiss
import sqlite3
import config
import numpy as np
from database.faiss_manager import FaissManager
from database.db_manager import DatabaseManager

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH)

# Get vector of an archdaily image
with db.get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT id FROM assets WHERE source_id=3 LIMIT 1")
    ad_id = cur.fetchone()[0]

# Faiss index does not support retrieving vectors by ID easily if it's IndexIDMap.
# We can use index.reconstruct(id) ONLY if the underlying index supports it.
# IndexFlatL2 supports reconstruct.
try:
    internal_id = -1
    # IndexIDMap maps external ID to internal ID. We have to find it manually or just search using a dummy vector.
    # Let's just create a dummy vector
    v = np.random.rand(1, 768).astype('float32')
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    distances, ids = faiss_mgr.search(v, k=100)
    
    with db.get_connection() as conn:
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in ids[0])
        cur.execute(f"SELECT source_id, COUNT(*) FROM assets WHERE id IN ({placeholders}) GROUP BY source_id", ids[0].tolist())
        print("Source distribution for random query:", cur.fetchall())

except Exception as e:
    print("Error:", e)
