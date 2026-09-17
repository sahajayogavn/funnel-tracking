import pytest

from tools.l5_adk_runtime import run_runner


class FailingAsyncRunner:
    async def run_async(self, **kwargs):
        raise ConnectionError("configured LLM endpoint is unreachable")
        yield  # pragma: no cover


def test_async_adk_connection_error_reaches_scheduler():
    with pytest.raises(ConnectionError, match="configured LLM endpoint"):
        run_runner(FailingAsyncRunner(), user_id="u", session_id="s", new_message=None)
