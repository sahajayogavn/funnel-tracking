def fetch_comments_from_post(post_id: str) -> list:
    """
    Fetch comments from a specific Facebook Fanpage post.
    Agents can use this info to reply directly or save seeker's info.
    """
    # Requires API keys to be set via environment variable
    print(f"Fetching comments for post_id: {post_id}")
    return [
        {
            "id": "comment_1",
            "text": "Khóa học này khi nào bắt đầu ạ?",
            "sender_id": "12345"
        }
    ]
