import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    "from PyQt6.QtWidgets import (\n    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QMessageBox,",
    "from PyQt6.QtWidgets import (\n    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QMessageBox, QPushButton,"
)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Added QPushButton import")
