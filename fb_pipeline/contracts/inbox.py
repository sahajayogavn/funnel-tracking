from dataclasses import dataclass, field
from typing import Any
import re
from urllib.parse import parse_qs, urlparse


CITY_KEYWORDS = {
    "Hà Nội": ["Hà Nội", "Ha Noi", "Vương Thừa Vũ", "Khương Đình", "Khương Trung",
                "Thanh Xuân", "Cầu Giấy", "Đống Đa", "Ba Đình", "Hoàn Kiếm",
                "Hai Bà Trưng", "Long Biên", "Hà nội"],
    "TP. Hồ Chí Minh": ["Hồ Chí Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Saigon",
                         "Xô Viết Nghệ Tĩnh", "Bình Thạnh", "Quận 1", "Quận 3",
                         "Quận 7", "Thủ Đức", "Gò Vấp", "Tân Bình", "HCM"],
    "Đà Nẵng": ["Đà Nẵng", "Da Nang", "Đà nẵng"],
    "Huế": ["Huế", "Hue"],
    "Hội An": ["Hội An", "Hoi An"],
    "Nghệ An": ["Nghệ An", "Nghe An", "Vinh"],
    "Hải Phòng": ["Hải Phòng", "Hai Phong"],
    "Online": ["online", "Online", "ONLINE", "zoom", "Zoom", "trực tuyến"],
}


@dataclass
class InboxMessage:
    sender: str
    content: str
    message_timestamp: str = ""
    seq: int = 0


@dataclass
class ThreadRecord:
    page_id: str
    thread_id: str
    thread_name: str
    preview_text: str
    thread_lines: list[str]
    dom_index: int


@dataclass
class SeekerInfo:
    name: str
    phone: str | None = None
    email: str | None = None
    city: str = "Unknown"
    lead_stage: str = "Intake"


@dataclass
class MasHandoff:
    thread_id: str
    thread_name: str
    page_id: str
    fb_url: str
    seeker: SeekerInfo
    ad_context: str = ""
    ad_ids: list[str] = field(default_factory=list)
    messages: list[InboxMessage] = field(default_factory=list)


@dataclass
class EnrichedThreadRecord(ThreadRecord):
    fb_url: str = ""
    ad_context: str = ""
    ad_ids: list[str] = field(default_factory=list)
    user_info: dict[str, Any] = field(default_factory=dict)
    city: str = "Unknown"
    messages: list[InboxMessage] = field(default_factory=list)
    mas_handoff: MasHandoff | None = None



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



def extract_user_info(messages: list, thread_name: str, ad_context: str = "") -> dict:
    all_text = " ".join([m.get("content", "") for m in messages]) + " " + ad_context

    phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
    email_match = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', all_text)

    customer_phone = None
    customer_email = None
    for message in messages:
        if message.get("sender") == "Customer":
            content = message.get("content", "")
            phones = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', content)
            emails = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', content)
            if phones and not customer_phone:
                customer_phone = phones[0]
            if emails and not customer_email:
                customer_email = emails[0]

    return {
        "phone": customer_phone or (phone_match[0] if phone_match else None),
        "email": customer_email or (email_match[0] if email_match else None),
    }



def parse_ad_ids(text: str) -> list:
    raw = re.findall(r'ad_id\.?(\d{5,})', text)
    return list(dict.fromkeys(raw))



def detect_city(ad_context: str, page_messages: list) -> str:
    search_text = ad_context
    for message in page_messages:
        if message.get("sender") == "Page":
            search_text += " " + message.get("content", "")

    for city, keywords in CITY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in search_text:
                return city
    return "Unknown"
