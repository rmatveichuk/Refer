import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_emit = "self.search_triggered.emit(text, img_path, threshold, sources)"
new_emit = "self.search_triggered.emit(text, img_path, threshold, sources, getattr(self, 'selected_tags', []))"

content = content.replace(old_emit, new_emit)

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched search_panel.py emit")