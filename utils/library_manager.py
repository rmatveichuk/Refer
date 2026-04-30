import zipfile
import json
import os
import shutil
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

def export_library(db_path, index_path, output_path):
    """
    Exports DB and FAISS index into a single .refpack (ZIP) file.
    """
    db_path = Path(db_path)
    index_path = Path(index_path)
    
    # Create manifest
    manifest = {
        "version": "1.0",
        "created_at": datetime.now().isoformat(),
        "app": "Refer",
        "db_name": db_path.name,
        "index_name": index_path.name
    }
    
    manifest_path = Path(output_path).parent / "manifest_temp.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)
        
    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(db_path, arcname="refer.db")
            zipf.write(index_path, arcname="refer_faiss.index")
            zipf.write(manifest_path, arcname="manifest.json")
    finally:
        if manifest_path.exists():
            os.remove(manifest_path)
            
    return True

def import_library_replace(package_path, extract_dir):
    """
    Extracts .refpack to extract_dir. Returns paths to extracted files.
    """
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(package_path, 'r') as zipf:
        zipf.extractall(extract_dir)
        
    db_path = extract_dir / "refer.db"
    index_path = extract_dir / "refer_faiss.index"
    
    if not db_path.exists() or not index_path.exists():
        # Cleanup
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise FileNotFoundError("Invalid .refpack: Missing DB or Index files.")
        
    return db_path, index_path

def relink_paths(db_path, new_root):
    """
    Mass updates all local_path and thumbnail_path entries in DB.
    For each path, it keeps the filename but changes the directory to new_root.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, local_path, thumbnail_path FROM assets")
    rows = cursor.fetchall()
    
    count = 0
    new_root_path = Path(new_root)
    
    for row in rows:
        updates = {}
        if row['local_path']:
            old_name = os.path.basename(row['local_path'])
            # Normalize slashes for Windows compatibility
            updates['local_path'] = str(new_root_path / old_name).replace('/', '\\')
        if row['thumbnail_path']:
            old_name = os.path.basename(row['thumbnail_path'])
            updates['thumbnail_path'] = str(new_root_path / old_name).replace('/', '\\')
            
        if updates:
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            params = list(updates.values()) + [row['id']]
            cursor.execute(f"UPDATE assets SET {set_clause} WHERE id = ?", params)
            count += 1
            
    conn.commit()
    conn.close()
    return count

def backup_database(db_path, backup_dir=None):
    """Creates a copy of the database file with a timestamp."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
        
    if not backup_dir:
        backup_dir = db_path.parent
    else:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"
    
    shutil.copy2(db_path, backup_path)
    return backup_path

def merge_libraries(current_db_mgr, external_db_path, external_index_path, faiss_mgr):
    """
    Merges external library into current one.
    Returns (merged_count, skipped_count).
    """
    import faiss
    import numpy as np
    
    # 1. Load external index
    try:
        ext_index = faiss.read_index(str(external_index_path))
    except Exception as e:
        logger.error(f"Failed to read external index: {e}")
        return 0, 0
        
    # Get ID map
    if hasattr(ext_index, 'id_map'):
        ext_ids = faiss.vector_to_array(ext_index.id_map)
    else:
        # If not an IDMap, we assume IDs are 0 to n-1
        ext_ids = np.arange(ext_index.ntotal, dtype=np.int64)
    
    # 2. Connect to external DB
    ext_conn = sqlite3.connect(external_db_path)
    ext_conn.row_factory = sqlite3.Row
    ext_cur = ext_conn.cursor()
    
    # 3. Get all assets from external DB
    ext_cur.execute("SELECT * FROM assets")
    ext_assets = ext_cur.fetchall()
    
    merged_count = 0
    skipped_count = 0
    
    for row in ext_assets:
        # Check if exists by URL or pHash in CURRENT DB
        with current_db_mgr.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets WHERE (original_url = ? AND original_url != '') OR (phash = ? AND phash != '')", (row['original_url'], row['phash']))
            if cur.fetchone():
                skipped_count += 1
                continue
            
            # Insert Asset (ignoring sources/projects for now to keep it simple and avoid ID conflicts)
            cur.execute('''
                INSERT INTO assets (original_url, local_path, thumbnail_path, phash, width, height, created_at, category, image_type, description, is_favorite)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (row['original_url'], row['local_path'], row['thumbnail_path'], row['phash'], 
                  row['width'], row['height'], row['created_at'], row['category'], row['image_type'], 
                  row['description'], row['is_favorite']))
            
            new_id = cur.lastrowid
            conn.commit()
            
            # 4. Extract vector from external FAISS and add to current FAISS with new_id
            old_id = row['id']
            try:
                # Find the actual index in FAISS if it's an IDMap
                # If IDs don't match row['id'], we have a problem.
                # Usually they do.
                vector = ext_index.reconstruct(int(old_id))
                faiss_mgr.add_vector_no_save(new_id, vector.reshape(1, -1))
                merged_count += 1
            except Exception as e:
                logger.error(f"Failed to merge vector for asset {old_id}: {e}")

    faiss_mgr.save_index()
    ext_conn.close()
    return merged_count, skipped_count
