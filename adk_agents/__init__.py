# code:agent-mas-001:init
__all__ = ["root_agent"]


def __getattr__(name):
    if name == "root_agent":
        from .agent import root_agent
        return root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
