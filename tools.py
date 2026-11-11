import json
import time

from .scout import insights


def handler_for(scout, name):
    insights.observe_usage_hook(scout)

    def handler(params, **kwargs):
        started = time.perf_counter()
        result = scout.call(name, params)
        response = json.dumps(result, ensure_ascii=False, allow_nan=False)
        insights.record_tool(scout, name, result, (time.perf_counter() - started) * 1000,
                             len(response.encode('utf-8')), session_id=kwargs.get('session_id'))
        return response
    handler.scout = scout
    return handler
