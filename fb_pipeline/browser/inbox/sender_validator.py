import re

def detect_sender(html_str: str, bg_color: str) -> str:
    """
    Validates sender detection using exact color-variance heuristics
    and explicit DOM layout markers.
    
    # SENDER DETECTION BUGFIX:
    Background: We historically used `is_gray` to detect if the background color was not plain gray (meaning it was the Page's blue bubble).
    However, Facebook's Business Suite now employs transparent `backgroundColor` paired with CSS gradient `backgroundImage` for Page messages.
    This caused all Page messages to evaluate as 'transparent' -> falls back to Customer!
    
    Fix: The frontend JavaScript parser now injects a structural `HAS_BG_IMAGE_INDICATOR_XX` string into `html_str` 
    whenever the message bubble is styled with `background-image`.
    This guarantees 100% resilient identification of Page messages directly from the gradient styling.
    """
    html_str = html_str or ""
    bg = (bg_color or "").strip()
    
    if 'HAS_BG_IMAGE_INDICATOR_XX' in html_str:
        return "Page"
    
    is_gray = True
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', bg)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        max_diff = max(abs(r-g), abs(r-b), abs(g-b))
        if max_diff > 20 and bg != 'rgba(0, 0, 0, 0)' and bg != 'transparent':
            is_gray = False
        elif r >= 253 and g >= 253 and b >= 253:
            is_gray = False

    if 'You sent' in html_str or 'Đã gửi' in html_str:
        return "Page"
    else:
        if not is_gray:
            return "Page"
        else:
            return "Customer"
