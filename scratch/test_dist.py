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

# Let's see the actual distances for the image search in the user's screenshot
# We can't use the exact image but we can simulate image-to-image search using a random image from db
with db.get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT id FROM assets WHERE source_id=3 LIMIT 1 OFFSET 10")
    ext_id = cur.fetchone()[0]

index = faiss_mgr.index
base_index = faiss.downcast_index(index.index)
internal_id = -1
for i in range(index.ntotal):
    if index.id_map.at(i) == ext_id:
        internal_id = i
        break

v = base_index.reconstruct(internal_id)
v = np.expand_dims(v, axis=0)

distances, ids = faiss_mgr.search(v, k=100)
print("Typical distances for image-to-image search:")
print("Top 1:", distances[0])
print("Top 10:", distances[9])
print("Top 50:", distances[49])
print("Top 100:", distances[99])
