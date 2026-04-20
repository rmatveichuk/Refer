import faiss
import sqlite3
import config
import numpy as np
from database.faiss_manager import FaissManager
from database.db_manager import DatabaseManager
from ai.engine import AiEngine

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH)
ai = AiEngine()

v = ai.get_text_embedding("building exterior architecture")
v = np.expand_dims(v, axis=0).astype('float32')

distances, ids = faiss_mgr.search(v, k=20)

print("Search with Text:")
ids_list = [int(x) for x in ids]
placeholders = ','.join('?' for _ in ids_list)
with db.get_connection() as conn:
    cur = conn.cursor()
    cur.execute(f"SELECT a.id, s.domain FROM assets a LEFT JOIN sources s ON a.source_id = s.id WHERE a.id IN ({placeholders})", ids_list)
    domain_map = {row[0]: row[1] for row in cur.fetchall()}

for d, i in zip(distances, ids):
    print(f"Dist: {d:.4f}, ID: {i}, Domain: {domain_map.get(int(i), 'Unknown')}")
