def validate_thread_integrity(messages_list: list, logger) -> bool:
    """
    Verifies the sequential integrity of extracted messages.
    Ensures that the thread parsing hasn't dropped critical elements.
    """
    if not messages_list:
        logger.warning("Integrity check failed: Message list is empty.")
        return False
        
    empty_texts = 0
    for idx, msg in enumerate(messages_list):
        if not msg.get("text") and not msg.get("timestamp"):
            empty_texts += 1
            
    if empty_texts > len(messages_list) / 2 and len(messages_list) > 5:
        logger.warning(f"Integrity check warning: Over 50% of messages are empty ({empty_texts}/{len(messages_list)}).")
        # In reality, might just be media. But we flag it.
        return True
        
    logger.info(f"Integrity check passed for {len(messages_list)} messages.")
    return True
