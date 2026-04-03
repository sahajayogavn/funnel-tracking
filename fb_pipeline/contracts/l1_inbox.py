from dataclasses import dataclass, field
from typing import Any
import re
from urllib.parse import parse_qs, urlparse


CITY_KEYWORDS = {
    "Hà Nội": ["Hà Nội", "Ha Noi", "Vương Thừa Vũ", "Khương Đình", "Khương Trung",
                "Thanh Xuân", "Cầu Giấy", "Đống Đa", "Ba Đình", "Hoàn Kiếm",
                "Hai Bà Trưng", "Long Biên", "Hà nội"],
    "TP. Hồ Chí Minh": ["Hồ Chí Minh", "TP.HCM", "TPHCM", "Sài Gòn", "Saigon",
                         "Bình Thạnh", "Quận 1", "Quận 3",
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
    sidebar_time_text: str = ""
    sidebar_time_kind: str = ""
    sidebar_identity_key: str = ""
    selected_item_id: str = ""


@dataclass
class SeekerInfo:
    name: str
    phone: str | None = None
    email: str | None = None
    city: str = "Unknown"
    lead_stage: str = "Intake"


@dataclass
# code:arch-schema-002
class MasHandoff:
    thread_id: str
    thread_name: str
    page_id: str
    fb_url: str
    seeker: SeekerInfo
    ad_context: str = ""
    ad_ids: list[str] = field(default_factory=list)
    messages: list[InboxMessage] = field(default_factory=list)
    temperature: str = "warm"
    cool_step: int = 0


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
    customer_texts = [m.get("content", "") for m in messages if m.get("sender") == "Customer"]
    all_text = " ".join(customer_texts) + " " + ad_context

    phone_match = re.findall(r'(?:0\d{9,10}|\+84\d{9,10})', all_text)
    email_match = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', all_text)

    return {
        "phone": phone_match[0] if phone_match else None,
        "email": email_match[0] if email_match else None,
    }



def parse_ad_ids(text: str) -> list:
    raw = re.findall(r'ad_id\.?(\d{5,})', text)
    return list(dict.fromkeys(raw))



def detect_city(ad_context: str, page_messages: list) -> str:
    # code:tool-citydetect-001:keyword-fix — scan ALL senders (Customer + Page)
    search_text = ad_context
    for message in page_messages:
        # Handle both 'content' (DB/normalized) and 'text' (JS-scraped) keys
        content = message.get("content", "") or message.get("text", "")
        search_text += " " + content

    for city, keywords in CITY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in search_text:
                return city
    return "Unknown"


# code:tool-citydetect-001:smart-detect
def detect_city_smart(ad_context: str, page_messages: list,
                      thread_name: str = "", customer_messages: list | None = None) -> str:
    """Detect city using LLM first, with rule-based fallback.

    Tries LLM-based classification (3-signal priority) if OPENAI_API_BASE
    and OPENAI_API_KEY env vars are set. Falls back to keyword-based
    detect_city() if LLM is unavailable or returns Unknown.

    Args:
        ad_context: Ad content text the user interacted with.
        page_messages: List of dicts with 'sender' and 'content' keys.
        thread_name: Name of the seeker (for LLM prompt).
        customer_messages: List of dicts with 'sender' and 'content' keys.
            If None, extracted from page_messages context.

    Returns:
        City name string (e.g. "Hà Nội", "TP. Hồ Chí Minh", "Unknown").
    """
    import logging
    import os
    logger = logging.getLogger("city_smart")

    api_base = os.environ.get("OPENAI_API_BASE", "")
    api_key = os.environ.get("OPENAI_API_KEY", "")

    if api_base and api_key:
        try:
            from fb_pipeline.contracts.l1_city_llm import detect_city_llm

            # Extract text lists for the LLM prompt
            cust_texts = []
            page_texts = []
            if customer_messages:
                for m in customer_messages:
                    content = m.get("content", "") or m.get("text", "")
                    if content and m.get("sender") == "Customer":
                        cust_texts.append(content)
            for m in page_messages:
                content = m.get("content", "") or m.get("text", "")
                if content and m.get("sender") == "Page":
                    page_texts.append(content)

            # Also extract customer messages from page_messages list if not provided separately
            if not cust_texts:
                for m in page_messages:
                    content = m.get("content", "") or m.get("text", "")
                    if content and m.get("sender") == "Customer":
                        cust_texts.append(content)

            model = os.environ.get("ADK_MODEL", "openai/gpt-5.4")
            # Strip "openai/" prefix for raw API call
            if model.startswith("openai/"):
                model = model[7:]

            result = detect_city_llm(
                thread_name=thread_name,
                customer_messages=cust_texts,
                page_messages=page_texts,
                ad_content=ad_context,
                api_base=api_base,
                api_key=api_key,
                model=model,
            )

            llm_city = result.get("city", "Unknown")
            confidence = result.get("confidence", "low")
            logger.debug(
                f"LLM city for '{thread_name}': {llm_city} "
                f"(confidence={confidence}, reasoning={result.get('reasoning', '')})"
            )

            if llm_city != "Unknown":
                return llm_city

            # LLM returned Unknown — fall through to keyword-based
            logger.debug(f"LLM returned Unknown for '{thread_name}', falling back to keywords")

        except Exception as e:
            logger.warning(f"LLM city detection failed for '{thread_name}': {e}, falling back to keywords")

    # Fallback: keyword-based detection
    return detect_city(ad_context, page_messages)


__all__ = [
    "CITY_KEYWORDS",
    "EnrichedThreadRecord",
    "InboxMessage",
    "MasHandoff",
    "SeekerInfo",
    "ThreadRecord",
    "detect_city",
    "detect_city_smart",
    "extract_user_info",
    "parse_ad_ids",
    "parse_page_id",
]

