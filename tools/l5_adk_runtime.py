"""Synchronous bridge for Google ADK agents."""
import asyncio


# code:tool-inbox-mas-001:adk-async-runtime
def run_runner(runner, **kwargs):
    """Collect ADK events while propagating LiteLLM errors to the caller.

    Google ADK's synchronous ``Runner.run`` starts an internal asyncio thread
    and does not re-raise exceptions from that thread. Calling ``run_async``
    directly lets the scheduler catch connection failures and continue.
    """
    if not hasattr(runner, "run_async"):
        # Compatibility for small test doubles and older ADK versions.
        return list(runner.run(**kwargs))

    async def collect_events():
        events = []
        async for event in runner.run_async(**kwargs):
            events.append(event)
        return events

    return asyncio.run(collect_events())
