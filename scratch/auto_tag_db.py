import sys
import logging
import sqlite3
import numpy as np
import time

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

import config
from database.db_manager import DatabaseManager
from database.faiss_manager import FaissManager
from ai.engine import AiEngine

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
ai = AiEngine()

print(f"Total vectors in FAISS: {faiss_mgr.index.ntotal}")

start_time = time.time()

# 1. Get embeddings for all tags in vocabulary
vocab_embeddings = {}
vocab_categories = {}
all_tags = []

for category, tags in config.TAG_VOCABULARY.items():
    for tag in tags:
        vocab_embeddings[tag] = ai.get_text_embedding(tag)
        vocab_categories[tag] = category
        all_tags.append(tag)

print(f"Embedded {len(all_tags)} vocabulary tags in {time.time() - start_time:.2f}s")

# 2. Get all vectors from FAISS
try:
    total = faiss_mgr.index.ntotal
    # Reconstruct vectors
    vectors = faiss_mgr.index.index.reconstruct_n(0, total)
    
    # Get IDs
    import faiss
    ids = faiss.vector_to_array(faiss_mgr.index.id_map)
    
    print(f"Reconstructed {len(vectors)} vectors and {len(ids)} ids in {time.time() - start_time:.2f}s")
except Exception as e:
    print(f"Error reconstructing vectors: {e}")
    sys.exit(1)

# 3. Compute similarities in batches
vocab_matrix = np.vstack([vocab_embeddings[t] for t in all_tags]).T  # Shape: (1152, N_tags)
print(f"Vocab matrix shape: {vocab_matrix.shape}")

similarities = np.dot(vectors, vocab_matrix)
print(f"Computed similarities in {time.time() - start_time:.2f}s")

# 4. Assign tags to assets
THRESHOLD = 0.035
assignments = [] # list of (asset_id, tag_name)

cat_to_indices = {}
for i, tag in enumerate(all_tags):
    cat = vocab_categories[tag]
    if cat not in cat_to_indices:
        cat_to_indices[cat] = []
    cat_to_indices[cat].append(i)

for img_idx in range(len(ids)):
    asset_id = int(ids[img_idx])
    img_sims = similarities[img_idx]
    
    for cat, t_indices in cat_to_indices.items():
        best_sim = -1.0
        best_tag_idx = -1
        
        for t_idx in t_indices:
            if img_sims[t_idx] > best_sim:
                best_sim = img_sims[t_idx]
                best_tag_idx = t_idx
                
        if best_tag_idx != -1 and best_sim > THRESHOLD:
            assignments.append((asset_id, all_tags[best_tag_idx]))

print(f"Generated {len(assignments)} tag assignments in {time.time() - start_time:.2f}s")

# 5. Save to database
with db.get_connection() as conn:
    cur = conn.cursor()
    # Ensure tags exist in tags table
    cur.executemany("INSERT OR IGNORE INTO tags (name) VALUES (?)", [(t,) for t in all_tags])
    
    # Get tag IDs mapping
    cur.execute("SELECT id, name FROM tags")
    tag_id_map = {row['name']: row['id'] for row in cur.fetchall()}
    
    # Insert assignments
    insert_data = [(a[0], tag_id_map[a[1]]) for a in assignments]
    cur.executemany("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", insert_data)
    
    conn.commit()

print(f"Saved to database in {time.time() - start_time:.2f}s")
