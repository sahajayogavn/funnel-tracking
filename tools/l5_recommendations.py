# code:tool-recommendations-001:generate-proposals
"""Safe MAS recommendation generator for human-in-the-loop approval.

Creates proposal actions in action_queue with status='pending'.
NEVER sends outbound Facebook messages directly.
Supported contexts:
- reply (reply message / comment)
- comment (proactive or response)
- warmup / reminders (dormant seeker re-engagement)
- event (city-matched event notification)
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fb_pipeline.persistence.l4_sqlite_store import get_db_connection
from tools.l5_action_queue import enqueue_action

logger = logging.getLogger("tools.recommendations")

DEFAULT_PAGE_ID = "1548373332058326"

# Stage-specific warmup templates
WARMUP_TEMPLATES = {
    "Intake": (
        "Xin chào bạn! Chúng mình có các lớp thiền Sahaja Yoga hoàn toàn MIỄN PHÍ "
        "hàng tuần. Bạn có muốn tìm hiểu thêm về thời gian và địa điểm không ạ? 🧘"
    ),
    "Seeker": (
        "Chào bạn! Bạn ơi, khóa học thiền Sahaja Yoga căn bản sắp bắt đầu buổi tiếp theo. "
        "Không biết bạn đã sắp xếp được thời gian tham gia chưa ạ? 🙏"
    ),
    "Seeker_Public_Program": (
        "Chào bạn! Tuần này chúng mình có buổi thiền cộng đồng đặc biệt. "
        "Rất mong được đón tiếp bạn tham gia cùng mọi người nhé! 🌸"
    ),
    "Seeker_18_Weeks": (
        "Chào bạn! Nhóm thiền 18 tuần rất nhớ bạn. Hãy tiếp tục duy trì thực hành đều đặn "
        "để cảm nhận sự bình an sâu sắc nhé! ✨"
    ),
    "Seed": (
        "Chào bạn! Chúc mừng bạn đã hoàn thành khóa học căn bản. Bạn có muốn tham gia buổi "
        "thiền tập thể tuần này để củng cố thói quen thiền định không ạ? 🌿"
    ),
}

DEFAULT_EVENT_TEMPLATE = (
    "Chào bạn! Sắp tới Sahaja Yoga có sự kiện '{event_name}' tại {city} vào ngày {event_date}. "
    "Chương trình hoàn toàn miễn phí, kính mời bạn cùng người thân tham gia trải nghiệm thiền định ạ! 🧘✨"
)


def generate_reply_recommendations(
    page_id: str = DEFAULT_PAGE_ID,
    target_thread_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Propose replies for unreplied threads."""
    conn = get_db_connection()
    try:
        # Find threads where last message is not from Page
        query = """
            SELECT t.id AS thread_id, t.thread_name,
                   m.sender, m.content, m.message_timestamp
            FROM threads t
            JOIN messages m ON m.id = (
                SELECT id FROM messages WHERE thread_id = t.id ORDER BY id DESC LIMIT 1
            )
            WHERE m.sender NOT IN ('Page', 'Auto_Page')
        """
        params: list[Any] = []
        if target_thread_id:
            query += " AND t.id = ?"
            params.append(target_thread_id)
        query += " ORDER BY t.last_synced_time DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        created = []
        for r in rows:
            thread_id = r["thread_id"]
            thread_name = r["thread_name"] or "Seeker"
            last_msg = r["content"] or ""

            # Check if there is already a pending action for this thread
            pending = conn.execute(
                "SELECT id FROM action_queue WHERE target_id = ? AND status = 'pending' AND queue_type = 'reply_message'",
                (thread_id,),
            ).fetchone()
            if pending:
                continue

            # Polite draft reply proposal
            reply_text = (
                f"Chào bạn {thread_name}! Cảm ơn bạn đã nhắn tin cho Thiền Sahaja Yoga Việt Nam. "
                "Tụi mình có các lớp thiền hoàn toàn miễn phí tại Hà Nội, TP.HCM, Đà Nẵng và Online qua Zoom. "
                "Bạn muốn tham gia lớp học trực tiếp hay online ạ? 🙏"
            )
            action_id = enqueue_action(
                queue_type="reply_message",
                page_id=page_id,
                target_type="thread",
                target_id=thread_id,
                target_name=thread_name,
                action_text=reply_text,
                payload={"source": "recommendation_engine", "trigger": "unreplied_message", "last_message": last_msg},
            )
            created.append({
                "action_id": action_id,
                "queue_type": "reply_message",
                "target_id": thread_id,
                "target_name": thread_name,
                "action_text": reply_text,
            })
        return created
    finally:
        conn.close()


def generate_comment_recommendations(
    page_id: str = DEFAULT_PAGE_ID,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Propose replies for recent comments."""
    conn = get_db_connection()
    try:
        # Check if comments table exists
        has_comments = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='comments'"
        ).fetchone()
        if not has_comments:
            return []

        rows = conn.execute(
            """
            SELECT c.id, c.commenter_name, c.comment_text, c.post_id
            FROM comments c
            WHERE c.commenter_name NOT LIKE '%Sahaja%'
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        created = []
        for r in rows:
            comment_id = str(r["id"])
            commenter_name = r["commenter_name"] or "Bạn"
            pending = conn.execute(
                "SELECT id FROM action_queue WHERE target_id = ? AND status = 'pending' AND queue_type = 'reply_comment'",
                (comment_id,),
            ).fetchone()
            if pending:
                continue

            reply_text = (
                f"Dạ chào {commenter_name}, lớp thiền Sahaja Yoga hoàn toàn miễn phí ạ. "
                "Page đã gửi thông tin chi tiết qua tin nhắn, bạn kiểm tra hộp thư giúp Page nhé! 🙏"
            )
            action_id = enqueue_action(
                queue_type="reply_comment",
                page_id=page_id,
                target_type="comment",
                target_id=comment_id,
                target_name=commenter_name,
                action_text=reply_text,
                payload={"source": "recommendation_engine", "post_id": r["post_id"], "comment_text": r["comment_text"]},
            )
            created.append({
                "action_id": action_id,
                "queue_type": "reply_comment",
                "target_id": comment_id,
                "target_name": commenter_name,
                "action_text": reply_text,
            })
        return created
    finally:
        conn.close()


def generate_warmup_recommendations(
    page_id: str = DEFAULT_PAGE_ID,
    target_thread_id: Optional[str] = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Propose warm-up / reminder messages for dormant seekers."""
    conn = get_db_connection()
    try:
        query = """
            SELECT u.thread_id, u.thread_name, u.city, u.lead_stage, u.last_interaction
            FROM users u
            WHERE u.thread_id IS NOT NULL
        """
        params: list[Any] = []
        if target_thread_id:
            query += " AND u.thread_id = ?"
            params.append(target_thread_id)
        query += " ORDER BY u.last_interaction ASC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        created = []
        for r in rows:
            thread_id = r["thread_id"]
            thread_name = r["thread_name"] or "Seeker"
            stage = r["lead_stage"] or "Intake"

            pending = conn.execute(
                "SELECT id FROM action_queue WHERE target_id = ? AND status = 'pending' AND queue_type = 'proactive_message'",
                (thread_id,),
            ).fetchone()
            if pending:
                continue

            message_text = WARMUP_TEMPLATES.get(stage, WARMUP_TEMPLATES["Intake"])
            action_id = enqueue_action(
                queue_type="proactive_message",
                page_id=page_id,
                target_type="thread",
                target_id=thread_id,
                target_name=thread_name,
                action_text=message_text,
                payload={"source": "recommendation_engine", "type": "warmup", "stage": stage, "city": r["city"]},
            )
            created.append({
                "action_id": action_id,
                "queue_type": "proactive_message",
                "target_id": thread_id,
                "target_name": thread_name,
                "action_text": message_text,
            })
        return created
    finally:
        conn.close()


def generate_event_recommendations(
    page_id: str = DEFAULT_PAGE_ID,
    target_thread_id: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Propose event invitation messages based on seeker city."""
    conn = get_db_connection()
    try:
        # Check upcoming events
        event = None
        has_events = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        if has_events:
            event = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()

        event_name = event["name"] if event else "Chương trình Thiền & Âm nhạc Mùa Thu"
        event_city = event["city"] if event else (city or "Hà Nội")
        event_date = event["event_date"] if event else "Chủ Nhật hàng tuần (20h00)"

        query = """
            SELECT u.thread_id, u.thread_name, u.city, u.lead_stage
            FROM users u
            WHERE u.thread_id IS NOT NULL
        """
        params: list[Any] = []
        if target_thread_id:
            query += " AND u.thread_id = ?"
            params.append(target_thread_id)
        elif city and city != "all":
            query += " AND (u.city = ? OR u.city = 'Unknown')"
            params.append(city)

        query += " ORDER BY u.last_interaction DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        created = []
        for r in rows:
            thread_id = r["thread_id"]
            thread_name = r["thread_name"] or "Seeker"

            pending = conn.execute(
                "SELECT id FROM action_queue WHERE target_id = ? AND status = 'pending' AND queue_type = 'proactive_message'",
                (thread_id,),
            ).fetchone()
            if pending:
                continue

            message_text = DEFAULT_EVENT_TEMPLATE.format(
                event_name=event_name,
                city=r["city"] if r["city"] != "Unknown" else event_city,
                event_date=event_date,
            )
            action_id = enqueue_action(
                queue_type="proactive_message",
                page_id=page_id,
                target_type="thread",
                target_id=thread_id,
                target_name=thread_name,
                action_text=message_text,
                payload={"source": "recommendation_engine", "type": "event", "event_name": event_name, "city": r["city"]},
            )
            created.append({
                "action_id": action_id,
                "queue_type": "proactive_message",
                "target_id": thread_id,
                "target_name": thread_name,
                "action_text": message_text,
            })
        return created
    finally:
        conn.close()


def generate_all_recommendations(
    page_id: str = DEFAULT_PAGE_ID,
    target_thread_id: Optional[str] = None,
    city: Optional[str] = None,
    limit_per_type: int = 3,
) -> list[dict[str, Any]]:
    """Run all recommendation generators and aggregate results."""
    results = []
    results.extend(generate_reply_recommendations(page_id, target_thread_id, limit=limit_per_type))
    results.extend(generate_warmup_recommendations(page_id, target_thread_id, limit=limit_per_type))
    results.extend(generate_event_recommendations(page_id, target_thread_id, city=city, limit=limit_per_type))
    if not target_thread_id:
        results.extend(generate_comment_recommendations(page_id, limit=limit_per_type))
    return results


def main():
    parser = argparse.ArgumentParser(description="MAS Safe Recommendation Engine")
    parser.add_argument("--page-id", default=DEFAULT_PAGE_ID, help="Target Facebook Page ID")
    parser.add_argument("--type", choices=["all", "reply", "comment", "warmup", "event"], default="all", help="Recommendation type")
    parser.add_argument("--thread-id", default=None, help="Target specific thread ID")
    parser.add_argument("--city", default=None, help="Filter by city")
    parser.add_argument("--limit", type=int, default=5, help="Max proposals to generate per category")
    parser.add_argument("--json", action="store_true", help="Output JSON format")

    args = parser.parse_args()

    if args.type == "reply":
        proposals = generate_reply_recommendations(args.page_id, args.thread_id, limit=args.limit)
    elif args.type == "comment":
        proposals = generate_comment_recommendations(args.page_id, limit=args.limit)
    elif args.type == "warmup":
        proposals = generate_warmup_recommendations(args.page_id, args.thread_id, limit=args.limit)
    elif args.type == "event":
        proposals = generate_event_recommendations(args.page_id, args.thread_id, city=args.city, limit=args.limit)
    else:
        proposals = generate_all_recommendations(args.page_id, args.thread_id, city=args.city, limit_per_type=args.limit)

    if args.json:
        print(json.dumps({"success": True, "count": len(proposals), "proposals": proposals}, ensure_ascii=False, indent=2))
    else:
        print(f"Generated {len(proposals)} proposal(s) into action_queue (status: pending).")
        for p in proposals:
            print(f"  [{p['queue_type']}] #{p['action_id']} for {p['target_name']}: {p['action_text'][:60]}...")


if __name__ == "__main__":
    main()
