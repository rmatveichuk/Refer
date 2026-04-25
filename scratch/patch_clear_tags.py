import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

new_clear_all = """
    def _clear_all(self):
        self.hybrid_input.clear_all()
        self.slider_sens.setValue(60)
        self.set_selected_tags([])  # Clear tags as well
"""

content = re.sub(
    r'def _clear_all\(self\):\n\s*self\.hybrid_input\.clear_all\(\)\n\s*self\.slider_sens\.setValue\(60\)',
    new_clear_all.strip(),
    content,
    flags=re.DOTALL
)

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched _clear_all to clear tags")