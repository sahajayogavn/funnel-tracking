"""Compatibility shim for tools/l5_scheduler.py."""
from tools.l5_scheduler import *  # noqa: F401,F403

if __name__ == "__main__":
    from tools.l5_scheduler import main
    main()
