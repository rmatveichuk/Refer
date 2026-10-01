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
    main()
