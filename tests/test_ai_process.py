"""Local-process protocol failure handling without downloading or loading weights."""
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from ai.process_client import AiProcessClient


SERVICE = r'''
import json,sys,time
print(json.dumps({'ok':True,'result':{'dimension':3,'token_limit':64}}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    argument=request['args'][0]
    if argument=='crash': sys.exit(1)
    if argument=='timeout': time.sleep(100)
    if argument=='error':
        print(json.dumps({'ok':False,'error':'test failure'}),flush=True)
        continue
    result={'effective_text':argument,'token_count':8,'token_limit':64,'truncated':False} if request['method']=='get_text_query_info' else [1,2,3]
    print(json.dumps({'ok':True,'result':result}),flush=True)
'''


class AiProcessTests(unittest.TestCase):
    def client(self, timeout=2):
        client = AiProcessClient(command=[sys.executable, '-u', '-c', SERVICE], timeout=timeout)
        self.addCleanup(client.close)
        return client

    def test_russian_request_and_vector_roundtrip(self):
        client = self.client()
        text = 'бетонный дом среди сосен'
        self.assertEqual(client.get_text_query_info(text)['effective_text'], text)
        self.assertEqual(client.text_token_limit, 64)
        vector = client.get_text_embedding(text)
        self.assertEqual(vector.dtype, np.float32)
        np.testing.assert_array_equal(vector, [1, 2, 3])

    def test_service_error_does_not_corrupt_next_request(self):
        client = self.client()
        with self.assertRaisesRegex(RuntimeError, 'test failure'):
            client.get_text_embedding('error')
        np.testing.assert_array_equal(client.get_text_embedding('next'), [1, 2, 3])

    def test_crash_is_reported_instead_of_waiting_indefinitely(self):
        client = self.client()
        with self.assertRaisesRegex(RuntimeError, 'завершился'):
            client.get_text_embedding('crash')

    def test_timeout_stops_only_owned_process(self):
        client = self.client(timeout=0.1)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'не ответила вовремя'):
            client.get_text_embedding('timeout')
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(client.process.poll())

    def test_concurrent_requests_keep_their_own_responses(self):
        client = self.client()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(client.get_text_query_info, ['дом', 'растение']))
        self.assertEqual([result['effective_text'] for result in results], ['дом', 'растение'])


if __name__ == '__main__':
    unittest.main()
