import sys
import re

with open('ui/widgets/hybrid_search_input.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_btn_style = """
        self.btn_analyze.setStyleSheet(\"\"\"
            QPushButton {
                background-color: #2d2d2d; color: #888; 
                border: 1px solid #444; border-radius: 4px; 
                padding: 4px 8px; font-weight: bold; font-size: 11px;
                margin: 0;
            }
            QPushButton:hover { 
                background-color: #3d3d3d; 
                color: #e0e0e0;
                border-color: #666;
            }
        \"\"\")
"""

content = re.sub(
    r'self\.btn_analyze\.setStyleSheet\("""\s*QPushButton \{.*?\}\s*QPushButton:hover \{.*?\}\s*"""\)',
    new_btn_style.strip(),
    content,
    flags=re.DOTALL
)

content = content.replace("self.preview_layout.addWidget(self.btn_analyze, alignment=Qt.AlignmentFlag.AlignHCenter)", "")

with open('ui/widgets/hybrid_search_input.py', 'w', encoding='utf-8') as f:
    f.write(content)


with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    sp_content = f.read()

sp_content = sp_content.replace(
    "tags_header_layout.addWidget(self.btn_manage_tags)\n        tags_header_layout.addStretch()",
    "tags_header_layout.addWidget(self.btn_manage_tags)\n        tags_header_layout.addWidget(self.hybrid_input.btn_analyze)\n        tags_header_layout.addStretch()"
)

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(sp_content)

print("Moved analyze button and changed style")