import re

def detect_sender(html_str: str, bg_color: str) -> str:
    """
    Validates sender detection using exact color-variance heuristics
    rather than fragile DOM classes.
    """
    html_str = html_str or ""
    bg = (bg_color or "").strip()
    
    is_gray = True
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', bg)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        max_diff = max(abs(r-g), abs(r-b), abs(g-b))
        if max_diff > 20 and bg != 'rgba(0, 0, 0, 0)' and bg != 'transparent':
            is_gray = False

    if 'You sent' in html_str or 'Đã gửi' in html_str:
        return "Page"
    else:
        if not is_gray:
            return "Page"
        else:
            return "Customer"
