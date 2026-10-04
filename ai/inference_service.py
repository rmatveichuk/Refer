"""Local inference service: CUDA runs on this process's main Python thread."""
import json
import sys
from ai.engine import AiEngine


def send(message):
    print(json.dumps(message, ensure_ascii=False), flush=True)


def main():
    try:
        engine = AiEngine()
        send({'ok': True, 'result': {'dimension': engine.model.config.text_config.hidden_size,
                                    'token_limit': engine.text_token_limit}})
    except Exception as error:
        send({'ok': False, 'error': str(error)})
        return
    allowed = {'get_text_query_info', 'get_text_embedding', 'get_image_embedding',
               'get_image_embeddings_batch', 'extract_tags'}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request['method'] not in allowed:
                raise ValueError('Unsupported inference request')
            result = getattr(engine, request['method'])(*request.get('args', []))
            if hasattr(result, 'tolist'):
                result = result.tolist()
            send({'ok': True, 'result': result})
        except Exception as error:
            send({'ok': False, 'error': str(error)})


if __name__ == '__main__':
    if '--server' in sys.argv:
        import argparse
        import os
        from pathlib import Path
        from ai.shared_service import InferenceServer

        parser = argparse.ArgumentParser(description="Refer Shared Inference Service")
        parser.add_argument('--server', action='store_true', help="Run TCP inference server")
        parser.add_argument('--state-dir', type=Path, default=None, help="Directory for ai_service.json registration")
        parser.add_argument('--host', type=str, default="127.0.0.1", help="Host interface to bind")
        parser.add_argument('--port', type=int, default=0, help="Port to bind (0 for dynamic port)")
        parser.add_argument('--fake-engine', action='store_true', help="Use lightweight mock engine for testing")
        args, _ = parser.parse_known_args()

        engine = None
        if args.fake_engine or os.environ.get("REFER_USE_FAKE_ENGINE") == "1":
            from tests.fake_engine import FakeEngine
            engine = FakeEngine()

        server = InferenceServer(engine=engine, host=args.host, port=args.port, state_dir=args.state_dir)
        server.start()
    else:
        main()
