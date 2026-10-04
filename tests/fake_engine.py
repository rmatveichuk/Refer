"""Lightweight fake engine for process-level IPC tests without loading PyTorch or GPU weights."""
import threading
from types import SimpleNamespace
import numpy as np


class FakeEngine:
    def __init__(self):
        self.model = SimpleNamespace(
            config=SimpleNamespace(
                text_config=SimpleNamespace(hidden_size=1152, max_position_embeddings=64)
            )
        )
        self.text_token_limit = 64
        self.call_count = 0
        self._lock = threading.Lock()

    def get_text_query_info(self, text: str):
        with self._lock:
            self.call_count += 1
        return {
            "effective_text": text,
            "token_count": len(text.split()),
            "token_limit": 64,
            "truncated": False
        }

    def get_text_embedding(self, text: str):
        with self._lock:
            self.call_count += 1
        if text == "error":
            raise ValueError("simulated inference failure")
        return np.ones(1152, dtype=np.float32) * float(len(text))

    def get_image_embedding(self, path: str):
        with self._lock:
            self.call_count += 1
        return np.ones(1152, dtype=np.float32) * 0.5

    def get_image_embeddings_batch(self, paths: list):
        with self._lock:
            self.call_count += 1
        return np.ones((len(paths), 1152), dtype=np.float32)

    def extract_tags(self, path: str, vocabulary=None):
        with self._lock:
            self.call_count += 1
        return [("concrete", 0.95), ("exterior", 0.88)]
