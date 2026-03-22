from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommentRecord:
    commenter_name: str
    comment_text: str
    comment_timestamp: str = ""
    fb_profile_url: str = ""
    fb_user_id: str = ""
    is_reply: int = 0
    comment_date: str = ""


@dataclass
class PostRecord:
    page_id: str
    post_id: str
    post_name: str
    preview_text: str
    post_lines: list[str]
    dom_index: int


@dataclass
class EnrichedPostRecord(PostRecord):
    post_url: str = ""
    comments: list[CommentRecord] = field(default_factory=list)
    user_info: dict[str, Any] = field(default_factory=dict)
    city: str = "Unknown"
