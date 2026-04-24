import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix order so Галерея and Таблица are first!
bad_order = """        self.tabs.addTab(gallery_tab, "Галерея")
        self.tabs.addTab(QWidget(), "Выделить все")
        self.tabs.addTab(QWidget(), "Удалить")"""

good_order = """        self.tabs.addTab(gallery_tab, "Галерея")"""

content = content.replace(bad_order, good_order)

bad_order2 = """        self.tabs.addTab(library_tab, "Таблица")"""
good_order2 = """        self.tabs.addTab(library_tab, "Таблица")
        
        # Fake tabs for actions
        self.tabs.addTab(QWidget(), "Выделить все")
        self.tabs.addTab(QWidget(), "Удалить")"""

content = content.replace(bad_order2, good_order2)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Fixed tab order")
