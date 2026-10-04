"""Bounded, serialized requests to an isolated local model process."""
import atexit
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from queue import Queue, Empty
from threading import Lock, Thread
from types import SimpleNamespace
import numpy as np


class AiProcessClient:
    def __init__(self, *, timeout=30, command=None):
        if command is None:
            from ai.shared_service import SharedAiClient
            self._shared = SharedAiClient(timeout=timeout)
            self.model = self._shared.model
            self.timeout = timeout
            return

        self._shared = None
        self.timeout = timeout
        self._lock = Lock()
        self._responses = Queue()
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        self.process = subprocess.Popen(command,
                                        cwd=Path(__file__).resolve().parents[1], env=environment,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8',
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self._readers = [Thread(target=self._read_responses, daemon=True),
                         Thread(target=self._read_errors, daemon=True)]
        for reader in self._readers:
            reader.start()
        try:
            ready = self._receive(timeout=max(timeout, 60))
            self.model = SimpleNamespace(config=SimpleNamespace(text_config=SimpleNamespace(
                hidden_size=ready['dimension'], max_position_embeddings=ready['token_limit'])))
        except Exception:
            self.close()
            raise
        atexit.register(self.close)

    def _read_responses(self):
        try:
            for line in self.process.stdout:
                self._responses.put(json.loads(line))
        except Exception as error:
            self._responses.put({'ok': False, 'error': str(error)})
        finally:
            self._responses.put({'ok': False, 'error': 'Процесс модели завершился. Перезапустите приложение.'})

    def _read_errors(self):
        for line in self.process.stderr:
            logging.getLogger(__name__).warning('Model: %s', line.rstrip())

    def _receive(self, timeout=None):
        try:
            response = self._responses.get(timeout=self.timeout if timeout is None else timeout)
        except Empty:
            self.close()
            raise RuntimeError('Модель не ответила вовремя. Перезапустите приложение.') from None
        if not response.get('ok'):
            raise RuntimeError(response.get('error', 'Ошибка процесса модели.'))
        return response['result']

    def _request(self, method, *args):
        with self._lock:
            if self.process.poll() is not None:
                raise RuntimeError('Процесс модели завершился. Перезапустите приложение.')
            try:
                self.process.stdin.write(json.dumps({'method': method, 'args': args}, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise RuntimeError('Не удалось отправить запрос модели.') from error
            return self._receive()

    @property
    def text_token_limit(self):
        if self._shared:
            return self._shared.text_token_limit
        return self.model.config.text_config.max_position_embeddings

    def get_text_query_info(self, text):
        if self._shared:
            return self._shared.get_text_query_info(text)
        return self._request('get_text_query_info', text)

    def get_text_embedding(self, text):
        if self._shared:
            return self._shared.get_text_embedding(text)
        return np.asarray(self._request('get_text_embedding', text), dtype=np.float32)

    def get_image_embedding(self, path):
        if self._shared:
            return self._shared.get_image_embedding(path)
        return np.asarray(self._request('get_image_embedding', str(path)), dtype=np.float32)

    def get_image_embeddings_batch(self, paths):
        if self._shared:
            return self._shared.get_image_embeddings_batch(paths)
        return np.asarray(self._request('get_image_embeddings_batch', [str(path) for path in paths]), dtype=np.float32)

    def extract_tags(self, path, vocabulary=None):
        if self._shared:
            return self._shared.extract_tags(path, vocabulary)
        return self._request('extract_tags', str(path), vocabulary)

    def close(self):
        if self._shared:
            self._shared.close()
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for reader in self._readers:
            reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        atexit.unregister(self.close)
