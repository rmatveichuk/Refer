
import sqlite3
import faiss
import numpy as np
from pathlib import Path
import tempfile
import sys
import os

# Add project root to sys.path to import config
sys.path.append(os.getcwd())
import config

def sync_faiss_with_db():
    db_path = config.DB_PATH
    faiss_path = config.FAISS_PATH
    
    print(f"Database: {db_path}")
    print(f"FAISS index: {faiss_path}")
    
    if not faiss_path.exists():
        print("Error: FAISS index file not found.")
        return

    # 1. Connect to SQLite and get valid IDs
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id FROM assets WHERE embedding_id IS NOT NULL")
    valid_ids = set(r['id'] for r in cur.fetchall())
    print(f"Valid Gemini IDs in SQLite: {len(valid_ids)}")

    # 2. Load current FAISS index
    print("Loading FAISS index...")
    index = faiss.read_index(str(faiss_path))
    print(f"Total vectors in file: {index.ntotal}")
    
    if not hasattr(index, 'id_map'):
        print("Error: Index is not an IndexIDMap. Cannot sync by IDs.")
        return

    faiss_ids = faiss.vector_to_array(index.id_map)
    
    # 3. Filter IDs
    to_keep = []
    for i, fid in enumerate(faiss_ids):
        if fid in valid_ids:
            to_keep.append(i)
    
    print(f"Vectors to keep: {len(to_keep)}")

    # 4. Create new clean index
    # We use the same dimension as existing index
    new_quantizer = faiss.IndexFlatL2(index.d)
    new_index = faiss.IndexIDMap(new_quantizer)
    
    if to_keep:
        # Extract vectors for to_keep
        # index.reconstruct(i) works for IndexFlatL2
        # But for IDMap we might need to be careful. 
        # Easier: get all vectors if index is small, or iterate.
        
        # Batch extract and add
        vectors = []
        ids = []
        for i in to_keep:
            vec = index.index.reconstruct(i)
            vectors.append(vec)
            ids.append(faiss_ids[i])
        
        vectors = np.array(vectors).astype('float32')
        ids = np.array(ids).astype('int64')
        
        new_index.add_with_ids(vectors, ids)
        print(f"Added {new_index.ntotal} vectors to new index.")
    
    # 5. Backup old index and save new
    backup_old = faiss_path.with_suffix('.index.bak')
    if faiss_path.exists():
        if backup_old.exists():
            os.remove(backup_old)
        os.rename(faiss_path, backup_old)
        print(f"Old index backed up to: {backup_old.name}")
    
    faiss.write_index(new_index, str(faiss_path))
    print(f"Clean index saved successfully to: {faiss_path}")
    print("Synchronization complete!")

if __name__ == "__main__":
    sync_faiss_with_db()
