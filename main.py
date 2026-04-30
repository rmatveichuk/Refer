import sys
import logging
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.setup_wizard import SetupWizard
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger("Refer")

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Refer")
    app.setApplicationDisplayName("Refer — Менеджер архитектурных референсов")

    # Проверка первого запуска (отсутствие базы данных)
    if not config.DB_PATH.exists():
        
        # Миграция старой базы (если она осталась в LocalAppData от старой версии)
        old_db_path = config.APP_LOCAL_DIR.parent / "ReferAssetManager" / "refer.db"
        old_index_path = config.APP_LOCAL_DIR.parent / "ReferAssetManager" / "refer_faiss.index"
        
        if old_db_path.exists() and old_index_path.exists():
            import shutil
            from pathlib import Path
            logger.info("Found old database in LocalAppData. Migrating...")
            
            # Копируем старые файлы в новые пути
            shutil.copy2(old_db_path, config.DB_PATH)
            shutil.copy2(old_index_path, config.FAISS_PATH)
            
            # Важно: копируем WAL и SHM файлы, чтобы не потерять последние данные
            old_wal = str(old_db_path) + "-wal"
            old_shm = str(old_db_path) + "-shm"
            if Path(old_wal).exists():
                shutil.copy2(old_wal, str(config.DB_PATH) + "-wal")
            if Path(old_shm).exists():
                shutil.copy2(old_shm, str(config.DB_PATH) + "-shm")
                
            logger.info("Migration successful. Starting main app.")
            # Пропускаем SetupWizard, так как база уже есть
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
