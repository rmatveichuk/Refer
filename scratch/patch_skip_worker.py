import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

# If search_tags exists but text/image are empty, we don't need to run a thread!
# We can skip the SearchWorker and just call _search_vectors directly!
# This prevents FAISS index from breaking by being accessed from multiple places
# and skips the thread entirely.

patch = """
        if not text and not img_path:
            # Only tags search
            self.search_threshold = threshold
            self.search_sources = sources
            self.search_tags = tags or []
            import numpy as np
            self._search_vectors(np.array([], dtype=np.float32), "Search by tags")
            return
            
        # Prevent concurrent searches - SigLIP model is not thread-safe
        if self.active_searcher:
            return
            
        self.status_label.setText("🔍 Обработка визуального запроса...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.search_threshold = threshold
        self.search_sources = sources
        self.search_tags = tags or []

        class SearchWorker(QRunnable):
"""

content = re.sub(
    r'# Prevent concurrent searches.*self\.search_tags = tags or \[\]\n\n\s*class SearchWorker\(QRunnable\):',
    patch.strip() + "\n\n        class SearchWorker(QRunnable):",
    content,
    flags=re.DOTALL
)

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched main_window.py to skip worker for tag-only search")