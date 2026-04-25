import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Change search_debounce behavior when only slider changes
import re

old_slider_logic = """
    def _on_slider_released(self):
        self._search_debounce.start()
"""

# Let's not trigger a full FAISS search just from slider release if it's currently doing nothing.
# Actually, the problem is most likely that search_triggered triggers a NEW search which kills the model or segfaults FAISS if it's currently searching.
# The SearchWorker is running in a QThreadPool, but SigLIP / FAISS are not thread-safe.

# Wait, `self.active_searcher` check in main_window.py:
# if self.active_searcher: return
# This correctly prevents concurrent searches.

# The issue is "программа все еще вылетает при прокрутке ползунка".
# "при прокрутке" -> `sliderReleased` emits `_on_slider_released`, which starts the 500ms debounce timer, which calls `_emit_search()`.
# If `_emit_search` is called while the previous search results are being rendered, maybe something breaks?
# Or maybe the empty vector array is sent again?
pass