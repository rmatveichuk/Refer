"""
Backup and restore system for CLIP embeddings.
Creates backups of FAISS index and database embedding_id mappings.
"""

import shutil
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).parent.parent / "backups"


def create_backup() -> dict:
    """
    Создает бекап текущих CLIP данных.
    
    Returns:
        dict с информацией о бекапе
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"clip_backup_{timestamp}"
    backup_path = BACKUP_DIR / backup_name
    
    # Создаем директорию
    backup_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Creating backup: {backup_name}")
    
    # 1. Бекап FAISS индекса
    faiss_backup_path = backup_path / "faiss.index"
    if config.FAISS_PATH.exists():
        shutil.copy2(config.FAISS_PATH, faiss_backup_path)
        logger.info(f"FAISS index backed up: {config.FAISS_PATH.stat().st_size / 1024:.1f} KB")
    else:
        logger.warning("FAISS index not found, skipping")
    
    # 2. Бекап mapping из базы данных (asset_id -> embedding_id)
    mapping_backup_path = backup_path / "embedding_mapping.json"
    try:
        from database.db_manager import DatabaseManager
        db = DatabaseManager(config.DB_PATH)
        
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, embedding_id FROM assets 
                WHERE embedding_id IS NOT NULL
            """)
            
            mapping = []
            for row in cur.fetchall():
                mapping.append({
                    "asset_id": row['id'],
                    "embedding_id": row['embedding_id']
                })
        
        with open(mapping_backup_path, 'w', encoding='utf-8') as f:
            json.dump({
                "timestamp": timestamp,
                "type": "clip_embeddings",
                "total_assets": len(mapping),
                "mapping": mapping
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Embedding mapping backed up: {len(mapping)} assets")
        
    except Exception as e:
        logger.error(f"Failed to backup embedding mapping: {e}")
    
    # 3. Информация о бекапе
    backup_info = {
        "name": backup_name,
        "timestamp": timestamp,
        "path": str(backup_path),
        "faiss_size_kb": faiss_backup_path.stat().st_size / 1024 if faiss_backup_path.exists() else 0,
        "total_assets": len(mapping) if 'mapping' in locals() else 0,
        "status": "completed"
    }
    
    # Сохраняем метаданные
    with open(backup_path / "backup_info.json", 'w', encoding='utf-8') as f:
        json.dump(backup_info, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Backup completed: {backup_name}")
    return backup_info


def restore_backup(backup_name: str) -> bool:
    """
    Восстанавливает данные из бекапа.
    
    Args:
        backup_name: имя директории бекапа
        
    Returns:
        True если успешно
    """
    backup_path = BACKUP_DIR / backup_name
    
    if not backup_path.exists():
        logger.error(f"Backup not found: {backup_name}")
        return False
    
    logger.info(f"Restoring from backup: {backup_name}")
    
    try:
        # 1. Восстанавливаем FAISS индекс
        faiss_backup = backup_path / "faiss.index"
        if faiss_backup.exists():
            shutil.copy2(faiss_backup, config.FAISS_PATH)
            logger.info(f"FAISS index restored from {faiss_backup.name}")
        
        # 2. Восстанавливаем mapping в базе данных
        mapping_backup = backup_path / "embedding_mapping.json"
        if mapping_backup.exists():
            from database.db_manager import DatabaseManager
            db = DatabaseManager(config.DB_PATH)
            
            with open(mapping_backup, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            mapping = data.get("mapping", [])
            
            with db.get_connection() as conn:
                cur = conn.cursor()
                for item in mapping:
                    conn.execute(
                        "UPDATE assets SET embedding_id = ? WHERE id = ?",
                        (item['embedding_id'], item['asset_id'])
                    )
                conn.commit()
            
            logger.info(f"Embedding mapping restored: {len(mapping)} assets")
        
        logger.info(f"Restore completed: {backup_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to restore backup: {e}")
        return False


def delete_backup(backup_name: str) -> bool:
    """
    Удаляет бекап.
    
    Args:
        backup_name: имя директории бекапа
        
    Returns:
        True если успешно
    """
    backup_path = BACKUP_DIR / backup_name
    
    if not backup_path.exists():
        logger.error(f"Backup not found: {backup_name}")
        return False
    
    try:
        shutil.rmtree(backup_path)
        logger.info(f"Backup deleted: {backup_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete backup: {e}")
        return False


def list_backups() -> list:
    """
    Возвращает список всех бекапов.
    
    Returns:
        list of dict с информацией о бекапах
    """
    if not BACKUP_DIR.exists():
        return []
    
    backups = []
    for backup_path in sorted(BACKUP_DIR.iterdir()):
        if backup_path.is_dir():
            info_file = backup_path / "backup_info.json"
            if info_file.exists():
                with open(info_file, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                backups.append(info)
            else:
                backups.append({
                    "name": backup_path.name,
                    "path": str(backup_path),
                    "status": "unknown"
                })
    
    return backups


def clear_clip_embeddings() -> int:
    """
    Очищает все CLIP эмбеддинги из базы данных и FAISS индекса.
    
    Returns:
        количество очищенных ассетов
    """
    from database.db_manager import DatabaseManager
    import faiss
    import numpy as np
    
    db = DatabaseManager(config.DB_PATH)
    cleared = 0
    
    # 1. Очищаем embedding_id в базе
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM assets WHERE embedding_id IS NOT NULL")
        cleared = cur.fetchone()['cnt']
        
        cur.execute("UPDATE assets SET embedding_id = NULL WHERE embedding_id IS NOT NULL")
        conn.commit()
    
    logger.info(f"Cleared {cleared} embedding IDs from database")
    
    # 2. Пересоздаем пустой FAISS индекс
    try:
        quantizer = faiss.IndexFlatL2(config.FAISS_PATH.parent.name)
        new_index = faiss.IndexIDMap(quantizer)
        faiss.write_index(new_index, str(config.FAISS_PATH))
        logger.info("FAISS index recreated (empty)")
    except Exception as e:
        logger.error(f"Failed to recreate FAISS index: {e}")
    
    return cleared


def get_current_embedding_type() -> str:
    """
    Определяет тип текущих эмбеддингов в базе.
    
    Returns:
        "clip", "gemini", "mixed", или "none"
    """
    from database.db_manager import DatabaseManager
    
    db = DatabaseManager(config.DB_PATH)
    
    with db.get_connection() as conn:
        cur = conn.cursor()
        
        # Проверяем embedding_id
        cur.execute("""
            SELECT COUNT(*) as cnt FROM assets WHERE embedding_id IS NOT NULL
        """)
        total_indexed = cur.fetchone()['cnt']
        
        if total_indexed == 0:
            return "none"
        
        # Проверяем FAISS индекс
        if config.FAISS_PATH.exists():
            try:
                index = faiss.read_index(str(config.FAISS_PATH))
                faiss_vectors = index.ntotal
                
                if faiss_vectors > 0:
                    # Предполагаем CLIP если нет явных маркеров Gemini
                    # В будущем можно добавить метаданные
                    return "clip"
            except:
                pass
        
        return "unknown"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== CLIP Backup System ===\n")
    
    # Список бекапов
    backups = list_backups()
    if backups:
        print(f"Found {len(backups)} backup(s):")
        for b in backups:
            print(f"  - {b['name']} ({b.get('total_assets', 0)} assets)")
    else:
        print("No backups found.\n")
    
    # Создание бекапа
    print("\nCreating backup...")
    info = create_backup()
    print(f"✓ Backup created: {info['name']}")
    print(f"  Assets: {info['total_assets']}")
    print(f"  FAISS size: {info['faiss_size_kb']:.1f} KB")
