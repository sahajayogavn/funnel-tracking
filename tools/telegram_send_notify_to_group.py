def send_notify_to_group(message_text: str, group_id: str = None) -> bool:
    """
    Send a notification to the staff/volunteers Telegram group.
    Typically used to alert members of a new incoming seeker message on Facebook.
    """
    # Logic to map incoming message info to a nice string and send to Telegram
    print(f"Sending notification to group [ID: {group_id}]: {message_text}")
    return True
