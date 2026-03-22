import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse


CACHE_TTL_SECONDS = 3600

CITY_KEYWORDS = {
    "Hà Nội": ["Hà Nội", "Ha Noi", "Vương Thừa Vũ", "Khương Đình", "Khương Trung",
                "Thanh Xuân", "Cầu Giấy", "Đống Đa", "Ba Đình", "Hoàn Kiếm",
                "Hai Bà Trưng", "Long Biên", "Hà nội"],
    "TP. Hồ Chí Minh": ["Hồ Chí Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Saigon",
                         "Bình Thạnh", "Quận 1", "Quận 3",
                         "Quận 7", "Thủ Đức", "Gò Vấp", "Tân Bình"],
    "Đà Nẵng": ["Đà Nẵng", "Da Nang", "Đà nẵng"],
    "Nghệ An": ["Nghệ An", "Nghe An", "Vinh"],
    "Hải Phòng": ["Hải Phòng", "Hai Phong"],
}


def parse_page_id(input_str: str) -> str:
    try:
        parsed = urlparse(input_str)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if 'asset_id' in qs:
                return qs['asset_id'][0]
    except Exception:
        pass
    if re.match(r'^\d+$', input_str):
        return input_str
    return input_str


def parse_post_id(input_str: str) -> str:
    try:
        parsed = urlparse(input_str)
        if parsed.query:
            qs = parse_qs(parsed.query)
            if 'selected_item_id' in qs:
                return qs['selected_item_id'][0]
    except Exception:
        pass
    return input_str


def extract_user_info(comments: list) -> dict:
    all_text = " ".join([c.get("comment_text", "") for c in comments])
    phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
    email_match = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', all_text)
    return {
        "phone": phone_match[0] if phone_match else None,
        "email": email_match[0] if email_match else None,
    }


def detect_city(text: str) -> str:
    for city, keywords in CITY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return city
    return "Unknown"


__all__ = [
    "CACHE_TTL_SECONDS",
    "CITY_KEYWORDS",
    "detect_city",
    "extract_user_info",
    "parse_page_id",
    "parse_post_id",
]
