import sys
import logging
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(name)s | %(message)s')

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
