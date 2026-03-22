import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from tools.l5_fetch_fb_messages import *
    from tools.l5_fetch_fb_messages import _scrape_inbox
except ModuleNotFoundError:
    from l5_fetch_fb_messages import *
    from l5_fetch_fb_messages import _scrape_inbox


if __name__ == "__main__":
    main()
