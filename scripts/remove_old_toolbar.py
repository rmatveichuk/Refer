import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Удаляем старый action_toolbar из _setup_gallery_tab
pattern_remove_toolbar = re.compile(
    r'        action_toolbar = QWidget\(\).*?gallery_layout\.addWidget\(action_toolbar\)\n',
    re.DOTALL
)
content = pattern_remove_toolbar.sub('', content)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Removed old toolbar")
