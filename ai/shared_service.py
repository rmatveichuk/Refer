"""Shared Inference Service and IPC Client for Refer.

Allows GUI and MCP server to connect to a single model process in VRAM,
avoiding model duplication and adhering to RTX 4060 8GB limits.
Requests are serialized on a single inference thread.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import config

logger = logging.getLogger(__name__)

SERVICE_INFO_FILE = "ai_service.json"
SERVICE_LOCK_FILE = "ai_service.lock"


def get_service_file_path(custom_dir: Optional[Path] = None) -> Path:
    base = custom_dir or config.APP_LOCAL_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / SERVICE_INFO_FILE


def get_lock_file_path(custom_dir: Optional[Path] = None) -> Path:
    base = custom_dir or config.APP_LOCAL_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / SERVICE_LOCK_FILE


def is_process_running(pid: int) -> bool:
    """Checks whether a process with given PID is currently active."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # Check exit code
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


class InferenceServer:
    """
    TCP server hosting the model on a single main worker thread.
    Serializes all inference requests from multiple clients (GUI, MCP, tests).
    """

    def __init__(self, engine=None, host: str = "127.0.0.1", port: int = 0, state_dir: Optional[Path] = None):
        self.host = host
        self.requested_port = port
        self.state_dir = state_dir
        self.engine = engine
        self.server_socket: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self.running = False
        self.request_queue: Queue[Tuple[dict, Queue]] = Queue()
        self._client_threads: List[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self, run_loop: bool = True):
        if self.engine is None:
            from ai.engine import AiEngine
            logger.info("Initializing AiEngine for shared inference service...")
            self.engine = AiEngine()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.requested_port))
        self.server_socket.listen(16)
        self.port = self.server_socket.getsockname()[1]
        self.running = True

        info = {
            "host": self.host,
            "port": self.port,
            "pid": os.getpid(),
            "dimension": getattr(self.engine.model.config.text_config, "hidden_size", 1152),
            "token_limit": getattr(self.engine, "text_token_limit", 64),
            "created_at": time.time()
        }

        info_path = get_service_file_path(self.state_dir)
        temp_path = info_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        temp_path.replace(info_path)
        logger.info(f"Shared inference service listening on {self.host}:{self.port} (PID: {os.getpid()})")

        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()

        if run_loop:
            self._worker_loop()

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.server_socket.accept()
                t = threading.Thread(target=self._client_handler, args=(client_sock,), daemon=True)
                t.start()
                with self._lock:
                    self._client_threads.append(t)
            except Exception:
                if not self.running:
                    break
                time.sleep(0.05)

    def _client_handler(self, client_sock: socket.socket):
        client_sock.settimeout(None)
        f = client_sock.makefile("r", encoding="utf-8")
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except Exception as err:
                    resp = {"ok": False, "error": f"Invalid JSON: {err}"}
                    client_sock.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                    continue

                resp_queue: Queue = Queue()
                self.request_queue.put((req, resp_queue))
                resp = resp_queue.get()
                client_sock.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception:
            pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass

    def _worker_loop(self):
        """Runs on the process main thread where CUDA/PyTorch model was instantiated."""
        while self.running:
            try:
                req, resp_queue = self.request_queue.get(timeout=0.5)
            except Empty:
                continue

            method = req.get("method")
            args = req.get("args", [])

            if method == "ping":
                resp_queue.put({"ok": True, "result": "pong"})
                continue

            if method == "info":
                resp_queue.put({
                    "ok": True,
                    "result": {
                        "dimension": getattr(self.engine.model.config.text_config, "hidden_size", 1152),
                        "token_limit": getattr(self.engine, "text_token_limit", 64)
                    }
                })
                continue

            if method == "shutdown":
                resp_queue.put({"ok": True, "result": "shutting_down"})
                self.stop()
                break

            try:
                fn = getattr(self.engine, method, None)
                if not fn or not callable(fn):
                    raise ValueError(f"Unsupported inference method: {method}")

                result = fn(*args)
                if hasattr(result, "tolist"):
                    result = result.tolist()
                elif isinstance(result, np.ndarray):
                    result = result.tolist()
                resp_queue.put({"ok": True, "result": result})
            except Exception as err:
                logger.warning(f"Error handling method {method}: {err}")
                resp_queue.put({"ok": False, "error": str(err)})

    def stop(self):
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        info_path = get_service_file_path(self.state_dir)
        try:
            if info_path.is_file():
                info_path.unlink(missing_ok=True)
        except Exception:
            pass


class ProcessFileLock:
    """Cross-process OS file lock using msvcrt (Windows) or fcntl (Unix).
    
    Provides true mutual exclusion across processes, ensures that lock is
    automatically released if the process crashes, and guarantees that callers
    who failed to acquire never delete the lock file.
    """
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._file = None
        self.acquired = False

    def acquire(self, timeout: float = 45.0, poll_interval: float = 0.1) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._try_acquire():
                self.acquired = True
                return True
            time.sleep(poll_interval)
        return False

    def _try_acquire(self) -> bool:
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.lock_path, "a+b")
            if sys.platform == "win32":
                import msvcrt
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except (OSError, IOError, PermissionError):
                    f.close()
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError):
                    f.close()
                    return False

            # Lock successfully acquired. Record PID and timestamp.
            f.seek(0)
            f.truncate(0)
            info = json.dumps({"pid": os.getpid(), "timestamp": time.time()}).encode("utf-8")
            f.write(info)
            f.flush()
            self._file = f
            return True
        except Exception:
            return False

    def release(self):
        if not self.acquired or not self._file:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            self._file.close()
            self._file = None
            self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        finally:
            self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock: {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class SharedAiClient:
    """
    Connects to the single shared inference server process over TCP loopback.
    If no server is running, launches it and waits for readiness.
    Provides identical interface to AiProcessClient.
    """

    def __init__(self, timeout: float = 30.0, state_dir: Optional[Path] = None, autostart: bool = True):
        self.timeout = timeout
        self.state_dir = state_dir
        self.autostart = autostart
        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._file = None
        self._spawned_proc: Optional[subprocess.Popen] = None
        self.model: Optional[SimpleNamespace] = None
        self._connect_or_spawn()

    def _ping(self, sock: socket.socket) -> bool:
        try:
            sock.settimeout(1.5)
            sock.sendall(b'{"method": "ping", "args": []}\n')
            f = sock.makefile("r", encoding="utf-8")
            line = f.readline()
            if not line:
                return False
            data = json.loads(line)
            return data.get("ok") is True and data.get("result") == "pong"
        except Exception:
            return False

    def _try_connect_existing(self) -> bool:
        info_path = get_service_file_path(self.state_dir)
        if not info_path.is_file():
            return False

        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            pid = info.get("pid", 0)
            port = info.get("port", 0)
            host = info.get("host", "127.0.0.1")

            if not is_process_running(pid):
                info_path.unlink(missing_ok=True)
                return False

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((host, port))
            if not self._ping(s):
                s.close()
                return False

            # Reset timeout to standard
            s.settimeout(self.timeout)
            self._sock = s
            self._file = s.makefile("r", encoding="utf-8")

            dim = info.get("dimension", 1152)
            limit = info.get("token_limit", 64)
            self.model = SimpleNamespace(config=SimpleNamespace(text_config=SimpleNamespace(
                hidden_size=dim, max_position_embeddings=limit)))
            return True
        except Exception:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                self._file = None
            return False

    def _connect_or_spawn(self):
        with self._lock:
            # 1. Fast path: attach to running service
            if self._try_connect_existing():
                return

            if not self.autostart:
                raise RuntimeError("Shared AI inference service is not running and autostart=False.")

            # 2. Acquire spawn lock to prevent duplicate Popen
            lock = ProcessFileLock(get_lock_file_path(self.state_dir))
            if not lock.acquire(timeout=45.0):
                if self._try_connect_existing():
                    return
                raise RuntimeError("Could not acquire AI service spawn lock within timeout.")

            try:
                # Double check existing again under lock
                if self._try_connect_existing():
                    return

                # 3. Spawn service subprocess
                env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
                cmd = [sys.executable, "-u", "-m", "ai.inference_service", "--server"]
                if self.state_dir is not None:
                    cmd.extend(["--state-dir", str(self.state_dir)])
                if os.environ.get("REFER_USE_FAKE_ENGINE") == "1":
                    cmd.append("--fake-engine")

                log_dir = self.state_dir or config.APP_LOCAL_DIR
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / "inference_service.log"
                log_file = open(log_path, "a", encoding="utf-8")

                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                cwd = Path(__file__).resolve().parents[1]

                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags
                )
                self._spawned_proc = proc

                # Wait for service info to be published
                info_path = get_service_file_path(self.state_dir)
                wait_deadline = time.monotonic() + 60.0
                connected = False
                try:
                    while time.monotonic() < wait_deadline:
                        if proc.poll() is not None:
                            raise RuntimeError(
                                f"AI inference service process (PID: {proc.pid}) exited unexpectedly with code {proc.returncode}. "
                                f"Check log at: {log_path}"
                            )
                        if info_path.is_file():
                            if self._try_connect_existing():
                                connected = True
                                break
                        time.sleep(0.2)

                    if not connected:
                        try:
                            proc.kill()
                            proc.wait(timeout=5.0)
                        except Exception:
                            pass
                        raise RuntimeError(f"AI inference service failed to initialize within timeout. Log: {log_path}")
                finally:
                    try:
                        log_file.close()
                    except Exception:
                        pass

            finally:
                lock.release()

    def _request(self, method: str, *args) -> Any:
        with self._lock:
            if not self._sock:
                if not self._try_connect_existing():
                    self._connect_or_spawn()

            payload = json.dumps({"method": method, "args": list(args)}, ensure_ascii=False) + "\n"
            try:
                self._sock.settimeout(self.timeout)
                self._sock.sendall(payload.encode("utf-8"))
                line = self._file.readline()
                if not line:
                    raise ConnectionResetError("Connection closed by AI inference service.")
                resp = json.loads(line)
            except Exception as err:
                self.close()
                raise RuntimeError(f"AI inference request failed: {err}") from err

            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "AI inference service reported an error."))
            return resp.get("result")

    @property
    def text_token_limit(self) -> int:
        if self.model and hasattr(self.model.config.text_config, "max_position_embeddings"):
            return self.model.config.text_config.max_position_embeddings
        return 64

    def get_text_query_info(self, text: str) -> Dict[str, Any]:
        return self._request("get_text_query_info", text)

    def get_text_embedding(self, text: str) -> np.ndarray:
        return np.asarray(self._request("get_text_embedding", text), dtype=np.float32)

    def get_image_embedding(self, path: str | Path) -> np.ndarray:
        return np.asarray(self._request("get_image_embedding", str(path)), dtype=np.float32)

    def get_image_embeddings_batch(self, paths: List[str | Path]) -> np.ndarray:
        return np.asarray(self._request("get_image_embeddings_batch", [str(p) for p in paths]), dtype=np.float32)

    def extract_tags(self, path: str | Path, vocabulary: Optional[List[str]] = None) -> List[Tuple[str, float]]:
        return self._request("extract_tags", str(path), vocabulary)

    def close(self):
        with self._lock:
            if self._file:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
