import sqlite3
import os
from pathlib import Path
import sys

# Добавляем корневую директорию в путь, чтобы импортировать config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def reset_index():
    print("=== REFER INDEX RESET TOOL ===")
    print(f"Target DB: {config.DB_PATH}")
    print(f"Target FAISS: {config.FAISS_PATH}")
    
    # 1. Сброс embedding_id в базе данных
    if config.DB_PATH.exists():
        print("-> Resetting embedding_id in database...")
        try:
            conn = sqlite3.connect(config.DB_PATH)
            conn.execute("UPDATE assets SET embedding_id = NULL")
            conn.commit()
            conn.close()
            print("   [OK] Database reset successful.")
        except Exception as e:
            print(f"   [ERROR] Failed to reset database: {e}")
    else:
        print("   [SKIP] Database not found.")

    # 2. Удаление файла индекса FAISS
    # Мы проверяем оба пути: короткий и полный
    paths_to_delete = [config.FAISS_PATH, Path(str(config.APP_DATA_DIR / "refer_faiss.index"))]
    
    for path in set(paths_to_delete):
        if path.exists():
            print(f"-> Attempting to delete: {path}")
            try:
                os.remove(path)
                print(f"   [OK] Deleted: {path}")
            except Exception as e:
                print(f"   [ERROR] Could not delete {path}: {e}")
                print("   [TIP] Make sure the Refer application is CLOSED before running this script.")
        else:
            print(f"   [SKIP] File not found: {path}")

    print("\nReset complete. Now you can restart the app and click 'Index' again.")

if __name__ == "__main__":
    reset_index()
