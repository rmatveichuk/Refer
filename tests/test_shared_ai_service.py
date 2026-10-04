"""Unit tests for the Shared AI Inference Service and TCP IPC."""
import json
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import numpy as np

from ai.shared_service import InferenceServer, SharedAiClient, get_service_file_path


class MockEngine:
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
        return {"effective_text": text, "token_count": len(text.split()), "token_limit": 64, "truncated": False}

    def get_text_embedding(self, text: str):
        with self._lock:
            self.call_count += 1
        if text == "error":
            raise ValueError("simulated inference failure")
        # Return deterministic dummy vector
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


class SharedAiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_test_shared_ai_"))
        self.mock_engine = MockEngine()
        self.server = InferenceServer(engine=self.mock_engine, port=0, state_dir=self.temp_dir)
        # Start server in thread
        self.server_thread = threading.Thread(target=self.server.start, kwargs={"run_loop": True}, daemon=True)
        self.server_thread.start()

        # Wait for info file to exist
        info_path = get_service_file_path(self.temp_dir)
        for _ in range(50):
            if info_path.is_file():
                break
            time.sleep(0.05)
        self.assertTrue(info_path.is_file(), "Server info file was not created.")

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_single_model_multiple_clients(self):
        """Verify two independent clients connect to the same server without starting a second model."""
        client1 = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        client2 = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        self.addCleanup(client1.close)
        self.addCleanup(client2.close)

        vec1 = client1.get_text_embedding("concrete")
        vec2 = client2.get_text_embedding("timber facade")

        self.assertEqual(vec1.shape, (1152,))
        self.assertEqual(vec2.shape, (1152,))
        self.assertEqual(vec1[0], float(len("concrete")))
        self.assertEqual(vec2[0], float(len("timber facade")))

        # Engine was called twice on the same shared engine
        self.assertEqual(self.mock_engine.call_count, 2)

    def test_error_handling_and_recovery(self):
        """Verify errors are cleanly raised as RuntimeError and subsequent calls succeed."""
        client = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        self.addCleanup(client.close)

        with self.assertRaises(RuntimeError) as ctx:
            client.get_text_embedding("error")
        self.assertIn("simulated inference failure", str(ctx.exception))

        # Next call works immediately
        vec = client.get_text_embedding("recovery")
        self.assertEqual(vec[0], float(len("recovery")))

    def test_concurrent_requests_serialized(self):
        """Verify concurrent requests from different threads/clients are processed correctly."""
        client1 = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        client2 = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        self.addCleanup(client1.close)
        self.addCleanup(client2.close)

        queries = [f"query_{i}" for i in range(10)]

        def worker(q):
            c = client1 if hash(q) % 2 == 0 else client2
            return c.get_text_query_info(q)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, queries))

        self.assertEqual(len(results), 10)
        for i, res in enumerate(results):
            self.assertEqual(res["effective_text"], f"query_{i}")

    def test_connection_reset_deadlock_regression(self):
        """P1: Connection reset must not deadlock on self._lock and thread must terminate within join(0.5)."""
        client = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        self.addCleanup(client.close)

        # Pre-warm connection
        client.get_text_embedding("init")

        # Mock sendall using wrapper to simulate abrupt connection drop
        class BrokenSocket:
            def __init__(self, real_sock):
                self._real = real_sock
            def __getattr__(self, name):
                return getattr(self._real, name)
            def sendall(self, data):
                raise ConnectionResetError("Simulated socket reset")

        client._sock = BrokenSocket(client._sock)

        error_caught = []
        def worker():
            try:
                client.get_text_embedding("test")
            except RuntimeError as err:
                error_caught.append(err)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=0.5)

        self.assertFalse(t.is_alive(), "Worker thread deadlocked and did not finish within 0.5s!")
        self.assertEqual(len(error_caught), 1)
        self.assertIn("Simulated socket reset", str(error_caught[0]))
        self.assertIsInstance(error_caught[0].__cause__, ConnectionResetError)

        # Subsequent call must reconnect and succeed
        vec = client.get_text_embedding("after_reset")
        self.assertEqual(vec.shape, (1152,))
        self.assertEqual(vec[0], float(len("after_reset")))

    def test_closed_client_subsequent_request(self):
        """Calling request on closed client reconnects cleanly without error."""
        client = SharedAiClient(timeout=5.0, state_dir=self.temp_dir, autostart=False)
        self.addCleanup(client.close)

        vec1 = client.get_text_embedding("first")
        self.assertEqual(vec1[0], float(len("first")))

        client.close()
        self.assertIsNone(client._sock)

        vec2 = client.get_text_embedding("second")
        self.assertEqual(vec2[0], float(len("second")))

    def test_process_file_lock_mutual_exclusion_and_release(self):
        """ProcessFileLock provides mutual exclusion and cleans up on release."""
        from ai.shared_service import ProcessFileLock
        lock_file = self.temp_dir / "test.lock"

        lock1 = ProcessFileLock(lock_file)
        self.assertTrue(lock1.acquire(timeout=1.0))

        lock2 = ProcessFileLock(lock_file)
        self.assertFalse(lock2.acquire(timeout=0.2, poll_interval=0.05))

        lock1.release()
        self.assertTrue(lock2.acquire(timeout=1.0))
        lock2.release()

    def test_process_level_shared_service_with_fake_engine(self):
        """P1: End-to-end OS subprocess spawn and connection using fake engine."""
        import os
        import signal
        from ai.shared_service import is_process_running

        proc_temp = Path(tempfile.mkdtemp(prefix="refer_test_proc_ai_"))
        self.addCleanup(lambda: shutil.rmtree(proc_temp, ignore_errors=True))

        old_env = os.environ.get("REFER_USE_FAKE_ENGINE")
        os.environ["REFER_USE_FAKE_ENGINE"] = "1"
        try:
            # Client 1 spawns the child process
            c1 = SharedAiClient(timeout=15.0, state_dir=proc_temp, autostart=True)
            self.addCleanup(c1.close)

            # Check that service info was written to proc_temp
            info_file = get_service_file_path(proc_temp)
            self.assertTrue(info_file.is_file())
            info = json.loads(info_file.read_text(encoding="utf-8"))
            server_pid = info["pid"]
            self.assertTrue(is_process_running(server_pid))

            # Client 2 attaches to the already running child process
            c2 = SharedAiClient(timeout=15.0, state_dir=proc_temp, autostart=False)
            self.addCleanup(c2.close)

            v1 = c1.get_text_embedding("proc_test_1")
            v2 = c2.get_text_embedding("proc_test_2")
            self.assertEqual(v1[0], float(len("proc_test_1")))
            self.assertEqual(v2[0], float(len("proc_test_2")))

            # Close client 1; client 2 still functions
            c1.close()
            v2_again = c2.get_text_embedding("proc_test_3")
            self.assertEqual(v2_again[0], float(len("proc_test_3")))

            # Shutdown server cleanly
            c2._request("shutdown")
            c2.close()

            # Wait for process exit
            if c1._spawned_proc:
                try:
                    c1._spawned_proc.wait(timeout=3.0)
                except Exception:
                    pass

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and is_process_running(server_pid):
                time.sleep(0.1)

            if is_process_running(server_pid):
                try:
                    os.kill(server_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                except Exception:
                    pass

        finally:
            if old_env is None:
                os.environ.pop("REFER_USE_FAKE_ENGINE", None)
            else:
                os.environ["REFER_USE_FAKE_ENGINE"] = old_env


if __name__ == "__main__":
    unittest.main()
