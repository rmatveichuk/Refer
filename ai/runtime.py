import sys


def create_engine():
    if sys.platform == 'win32':
        from ai.process_client import AiProcessClient
        return AiProcessClient()
    from ai.engine import AiEngine
    return AiEngine()
