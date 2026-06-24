import sys
import logging
from PyQt6.QtWidgets import QApplication, QMessageBox
from ui.main_window import MainWindow
from ui.setup_wizard import SetupWizard
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger("Refer")

def main():
    app = QApplication(sys.argv)
    
    # --- Одиночный запуск (Single Instance) ---
    import os
    from PyQt6.QtCore import QLockFile, QDir
    lock_file = QLockFile(os.path.join(QDir.tempPath(), "refer_v1.lock"))
    if not lock_file.tryLock(100):
        QMessageBox.warning(None, "Refer", "Программа уже запущена!")
        return
    app.setApplicationName("Refer")
    app.setApplicationDisplayName("Refer — Менеджер архитектурных референсов")

    # Проверка первого запуска (отсутствие базы данных)
    if not config.DB_PATH.exists():
        
        # Миграция старой базы (если она осталась в LocalAppData от старой версии)
        old_app_local = config.APP_LOCAL_DIR.parent / config.OLD_APP_NAME
        old_db_path = old_app_local / "refer.db"
        old_index_path = old_app_local / "refer_faiss.index"
        
        if old_db_path.exists() and old_index_path.exists():
            import shutil
            from pathlib import Path
            from utils.library_manager import relink_paths
            
            logger.info(f"Found old database in {config.OLD_APP_NAME}. Migrating...")
            
            # 1. Копируем старые файлы базы в новые пути
            shutil.copy2(old_db_path, config.DB_PATH)
            shutil.copy2(old_index_path, config.FAISS_PATH)
            
            # Копируем WAL и SHM файлы
            old_wal = str(old_db_path) + "-wal"
            old_shm = str(old_db_path) + "-shm"
            if Path(old_wal).exists():
                shutil.copy2(old_wal, str(config.DB_PATH) + "-wal")
            if Path(old_shm).exists():
                shutil.copy2(old_shm, str(config.DB_PATH) + "-shm")

            # 2. Перенос миниатюр (Thumbnails)
            # Проверяем оба варианта написания: Thumbnails (новое) и thumbnails (старое)
            for thumb_name in ["Thumbnails", "thumbnails"]:
                old_thumbs_dir = old_app_local / thumb_name
                if old_thumbs_dir.exists() and any(old_thumbs_dir.iterdir()):
                    logger.info(f"Moving thumbnails from {old_thumbs_dir}...")
                    for thumb_file in old_thumbs_dir.glob("*"):
                        if thumb_file.is_file():
                            dest_file = config.THUMBNAILS_DIR / thumb_file.name
                            if not dest_file.exists():
                                shutil.copy2(thumb_file, dest_file)
            
            # 3. Обновление путей в базе данных (Relinking)
            # Так как мы перенесли всё в стандартную папку текущей версии,
            # вызываем relink_paths, чтобы обновить абсолютные пути в колонке thumbnail_path
            logger.info("Relinking paths in database...")
            relinked_count = relink_paths(config.DB_PATH, new_thumbnails_root=str(config.THUMBNAILS_DIR))
            logger.info(f"Migration successful. Relinked {relinked_count} paths. Starting main app.")
        else:
            logger.info("First run detected. Launching SetupWizard...")
            wizard = SetupWizard()
            if wizard.exec() == SetupWizard.DialogCode.Accepted:
                logger.info("Setup complete.")
            else:
                logger.info("Setup cancelled by user. Exiting.")
                sys.exit(0)

    # Запуск основного приложения
    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
