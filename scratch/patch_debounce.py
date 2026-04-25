import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Only start debounce if there's actually something to search for!
patch = """
    def _on_slider_released(self):
        text = self.hybrid_input.text_input.text().strip()
        img = self.hybrid_input.image_path
        tags = getattr(self, 'selected_tags', [])
        
        # Запускаем автоматический поиск при отпускании ползунка
        # ТОЛЬКО если есть хотя бы 1 параметр поиска (текст, картинка или тег)
        if text or img or tags:
            self._search_debounce.start()
"""

import re
content = re.sub(
    r'def _on_slider_released\(self\):\n\s*self\._search_debounce\.start\(\)',
    patch.strip(),
    content,
    flags=re.DOTALL
)

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched slider debounce logic")