import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Connect signal in MainWindow
if "self.search_panel.extract_tags_requested.connect(self._extract_tags_from_image)" not in content:
    content = content.replace("self.search_panel.manage_tags_requested.connect(self._open_tag_manager)", "self.search_panel.manage_tags_requested.connect(self._open_tag_manager)\n        self.search_panel.extract_tags_requested.connect(self._extract_tags_from_image)")

# 2. Add methods
extract_methods = """
    @pyqtSlot(str)
    def _extract_tags_from_image(self, image_path: str):
        if not image_path:
            return
            
        if self.active_searcher:
            return
            
        self.status_label.setText("Анализ изображения (Zero-Shot Classification)...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        class ExtractTagsWorker(QRunnable):
            class Signals(QObject):
                result = pyqtSignal(list)
                error = pyqtSignal(str)

            def __init__(self, ai, img_path):
                super().__init__()
                self.ai = ai
                self.img_path = img_path
                self.signals = self.Signals()

            def run(self):
                try:
                    if not self.ai:
                        from ai.engine import AiEngine
                        self.ai = AiEngine()
                        
                    tags = self.ai.extract_tags(self.img_path)
                    self.signals.result.emit(tags)
                except Exception as e:
                    self.signals.error.emit(str(e))

        worker = ExtractTagsWorker(self.ai, image_path)
        worker.signals.result.connect(self._on_extract_tags_result)
        worker.signals.error.connect(self._on_search_error)
        self.active_searcher = worker
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(list)
    def _on_extract_tags_result(self, new_tags: list):
        self.active_searcher = None
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Извлечено тегов: {len(new_tags)}")
        
        if not new_tags:
            return
            
        current_tags = set(getattr(self.search_panel, 'selected_tags', []))
        for t in new_tags:
            current_tags.add(t)
            
        self.search_panel.set_selected_tags(list(current_tags))
        # Сразу запускаем поиск по новым тегам
        self.search_panel._emit_search()
"""

if "def _extract_tags_from_image" not in content:
    content = content.replace("    def _open_tag_manager(self):", extract_methods + "\n    def _open_tag_manager(self):")

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched main_window.py for tag extraction")