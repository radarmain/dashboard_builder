import time

from app.routes_chat import THINKING, _sse


def test_first_event_is_immediate_and_heartbeat_fires_during_slow_work() -> None:
    def slow():
        yield THINKING
        time.sleep(0.35)  # a "long tool call"
        yield {"type": "done"}

    lines = list(_sse(slow(), heartbeat=0.1))
    assert lines[0].startswith("data: ") and "Thinking..." in lines[0]
    assert any(line.startswith(": keep-alive") for line in lines)
    assert lines[-1].startswith("data: ") and '"done"' in lines[-1]


def test_worker_exception_becomes_error_event() -> None:
    def boom():
        yield THINKING
        raise RuntimeError("x")

    lines = list(_sse(boom(), heartbeat=1))
    assert '"error"' in lines[1] and '"done"' in lines[2]
