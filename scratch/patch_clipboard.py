import sys
import re

with open('ui/widgets/hybrid_search_input.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Imports
if "from PyQt6.QtGui import QGuiApplication" not in content:
    content = content.replace("from PyQt6.QtGui import QPixmap", "from PyQt6.QtGui import QPixmap, QGuiApplication, QKeySequence, QImage\nfrom PyQt6.QtCore import QEvent\nimport tempfile")

# 2. Add Remove Image Button & Container
ui_mod = """
        self.preview_layout = QVBoxLayout()
        self.preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_layout.setContentsMargins(0,0,0,0)

        # Container for image and close button
        self.img_container = QWidget()
        self.img_container.setFixedSize(100, 100)
        self.img_container.setVisible(False)
        
        self.lbl_preview = QLabel(self.img_container)
        self.lbl_preview.setGeometry(0, 0, 100, 100)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("border: none; background: transparent;")
        
        self.btn_remove_img = QPushButton("✕", self.img_container)
        self.btn_remove_img.setGeometry(80, 0, 20, 20)
        self.btn_remove_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove_img.setStyleSheet(\"\"\"
            QPushButton {
                background-color: rgba(0,0,0,0.6); color: white; border: none;
                border-radius: 10px; font-weight: bold; font-size: 10px;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #f44336; }
        \"\"\")
        self.btn_remove_img.clicked.connect(self.clear_image)
        
        self.preview_layout.addWidget(self.img_container)
"""

content = re.sub(
    r'self\.preview_layout = QVBoxLayout\(\).*?self\.preview_layout\.addWidget\(self\.lbl_preview\)',
    ui_mod.strip(),
    content,
    flags=re.DOTALL
)

# 3. Update set_image and clear_image
content = content.replace("self.lbl_preview.setVisible(True)", "self.img_container.setVisible(True)")
content = content.replace("self.lbl_preview.setVisible(False)", "self.img_container.setVisible(False)")

# 4. Clipboard Logic
clipboard_code = """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                if self._handle_paste():
                    return True # Intercept paste if it was an image
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            if self._handle_paste():
                return
        super().keyPressEvent(event)

    def _handle_paste(self):
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        
        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                temp_path = os.path.join(tempfile.gettempdir(), "refer_pasted_image.jpg")
                image.save(temp_path, "JPG")
                self.set_image(temp_path)
                return True
        elif mime.hasUrls():
            urls = mime.urls()
            if urls and urls[0].toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                self.set_image(urls[0].toLocalFile())
                return True
        return False
"""

if "def eventFilter" not in content:
    content += "\n" + clipboard_code

# Install event filter on text input & focus policies
init_ext = """
        self.text_input.returnPressed.connect(self._on_enter)
        self.layout.addWidget(self.text_input)

        # Enable Paste support
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.drop_zone.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.text_input.installEventFilter(self)
"""
content = content.replace(
    "self.text_input.returnPressed.connect(self._on_enter)\n        self.layout.addWidget(self.text_input)",
    init_ext.strip()
)

with open('ui/widgets/hybrid_search_input.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched hybrid_search_input.py for remove button and clipboard")