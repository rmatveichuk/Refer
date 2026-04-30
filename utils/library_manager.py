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
    
    # Create checkpoint to merge WAL into main DB to ensure all data is exported
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    # Create manifest
    manifest = {
        "version": "1.0",
        "created_at": datetime.now().isoformat(),
        "app": "Refer",
        "db_name": db_path.name,
        "index_name": index_path.name
    }
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(db_path, arcname="refer.db")
        zipf.write(index_path, arcname="refer_faiss.index")
        zipf.writestr("manifest.json", json.dumps(manifest, indent=4))
            
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

def relink_paths(db_path, new_local_root=None, new_thumbnails_root=None):
    """
    Updates local_path and thumbnail_path entries in DB.
    - new_thumbnails_root: If provided, all thumbnails are repointed here (flattened, as thumbnails are flat).
    - new_local_root: If provided, attempts to replace the common prefix of all local_paths with this new root, preserving relative structure.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, local_path, thumbnail_path FROM assets")
    rows = cursor.fetchall()
    
    # Find common prefix for local paths to replace it
    local_paths = [r['local_path'] for r in rows if r['local_path']]
    common_prefix = ""
    if local_paths and new_local_root:
        try:
            # os.path.commonpath needs valid paths, we normalize them first
            valid_paths = [Path(p.replace('\\', '/')) for p in local_paths]
            common_prefix = str(Path(*os.path.commonprefix([p.parts for p in valid_paths])))
        except Exception as e:
            logger.warning(f"Could not determine common prefix: {e}")
            common_prefix = ""
            
    count = 0
    if new_local_root:
        new_local_root_path = Path(new_local_root)
    if new_thumbnails_root:
        new_thumb_root_path = Path(new_thumbnails_root)
    
    for row in rows:
        updates = {}
        
        # Relink local_path (preserve relative structure)
        if row['local_path'] and new_local_root:
            old_path = str(Path(row['local_path'].replace('\\', '/')))
            if common_prefix and old_path.startswith(common_prefix):
                # Replace common prefix with new root
                rel_path = old_path[len(common_prefix):].lstrip('/\\')
                updates['local_path'] = str(new_local_root_path / rel_path).replace('/', '\\')
            else:
                # Fallback if common prefix fails: just put in root
                old_name = os.path.basename(row['local_path'])
                updates['local_path'] = str(new_local_root_path / old_name).replace('/', '\\')
                
        # Relink thumbnail_path (thumbnails are always flat)
        if row['thumbnail_path'] and new_thumbnails_root:
            old_name = os.path.basename(row['thumbnail_path'])
            updates['thumbnail_path'] = str(new_thumb_root_path / old_name).replace('/', '\\')
            
        if updates:
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            params = list(updates.values()) + [row['id']]
            cursor.execute(f"UPDATE assets SET {set_clause} WHERE id = ?", params)
            count += 1
            
    conn.commit()
    conn.close()
    return count

def backup_database(db_path, backup_dir=None):
    """Creates a copy of the database file with a timestamp, ensuring WAL is flushed."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
        
    if not backup_dir:
        backup_dir = db_path.parent
    else:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        
    # Checkpoint WAL before copying
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
        
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
