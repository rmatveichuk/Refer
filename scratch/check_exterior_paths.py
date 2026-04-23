import sqlite3
from pathlib import Path

db_path = Path(r"C:\Users\RMatv\AppData\Local\ReferAssetManager\refer.db")
if not db_path.exists():
    print("DB not found")
else:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Ищем ассеты, в пути которых есть "Экстерьер"
    cur.execute("SELECT local_path FROM assets WHERE local_path LIKE '%Экстерьер%' LIMIT 10")
    rows = cur.fetchall()
    print("Sample paths with 'Экстерьер':")
    for row in rows:
        print(f" - {row[0]}")
    conn.close()
