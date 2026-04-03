import json
import logging
import asyncio
from tools.l5_inbox_mas_context import load_knowledge_context

logger = logging.getLogger("inbox_mas_pipeline")

def run_adk_pipeline(thread_messages: list, seeker_context: dict, feedback: str = None) -> dict:
    """Run the ADK classifier + responder pipeline on a thread.

    Args:
        thread_messages: List of messages [{sender, content, timestamp}].
        seeker_context: Seeker profile dict from CRM lookup.
        feedback: Optional human feedback for rewriting the reply.

    Returns:
        dict: {classification, reply_text} from the pipeline.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from adk_agents.agent import root_agent

    # Format conversation for the agent
    conversation_text = "\n".join([
        f"[{m['sender']}] {m['content']}"
        for m in thread_messages
        if m.get('content')
    ])
    seeker_text = json.dumps(seeker_context, ensure_ascii=False, indent=2)
    knowledge_context = load_knowledge_context()

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="sahajayoga_inbox",
        session_service=session_service,
    )
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    session = loop.run_until_complete(
        session_service.create_session(
            app_name="sahajayoga_inbox",
            user_id="inbox_runner",
            state={
                "thread_messages": conversation_text,
                "seeker_context": seeker_text,
                "knowledge_context": knowledge_context,
            },
        )
    )

    prompt = (
        "Process this Facebook inbox thread using the provided session state. "
        "Use thread_messages, seeker_context, and knowledge_context when relevant."
    )
    if feedback:
        prompt += f"\n\nIMPORTANT HUMAN FEEDBACK FOR REVISION:\n{feedback}\nPlease rewrite the reply adhering strictly to this feedback."

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=prompt)]
    )

    result = {
        "classification": "",
        "reply_text": "",
        "thread_messages": conversation_text,
        "seeker_context": seeker_text,
        "knowledge_context": knowledge_context,
    }

    for event in runner.run(
        user_id="inbox_runner",
        session_id=session.id,
        new_message=user_msg
    ):
        if hasattr(event, 'content') and event.content and event.content.parts:
            text = event.content.parts[0].text
            if hasattr(event, 'author') and event.author == "MessageClassifier":
                result["classification"] = text
            elif hasattr(event, 'author') and event.author == "Responder":
                result["reply_text"] = text
            else:
                result["reply_text"] = text

    # code:tool-inbox-mas-001:reply-sanitizer
    # Strip any LLM chain-of-thought / reasoning lines from the reply before
    # it is typed into a Facebook message box. Lines that start with "**" or
    # match known reasoning patterns are removed. Only clean reply lines remain.
    result["reply_text"] = _sanitize_reply(result.get("reply_text", ""))

    return result


def run_adk_batch_pipeline(batch_payload: list, feedback: str = None) -> list:
    """Run the ADK BatchInboxAgent for a list of grouped threads.
    
    Args:
        batch_payload: List of dicts representing N threads and their messages.
        feedback: Optional HITL feedback text.
        
    Returns:
        List of dicts: [ { "thread_id": str, "classification": str, "reply_text": str } ]
    """
    from adk_agents.agent import batch_inbox_agent
    from google.adk.runners import Runner
    import json
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    import asyncio

    session_service = InMemorySessionService()
    runner = Runner(
        agent=batch_inbox_agent,
        app_name="sahajayoga_batch_inbox",
        session_service=session_service,
    )
    
    knowledge_context = load_knowledge_context()

    # Create a simplified version of payload to save token overhead
    simplified_payload = []
    for item in batch_payload:
        simplified_payload.append({
            "thread_id": item["thread_id"],
            "seeker_context": item.get("seeker", {}),
            "messages": item.get("messages", [])
        })

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    session = loop.run_until_complete(
        session_service.create_session(
            app_name="sahajayoga_batch_inbox",
            user_id="inbox_batch_runner",
            state={
                "batch_payload": json.dumps(simplified_payload, ensure_ascii=False),
                "knowledge_context": knowledge_context
            },
        )
    )

    prompt = (
        "Process this BATCH of Facebook inbox threads using the provided session state. "
        "Use batch_payload and knowledge_context. Remember to output EXACTLY a valid JSON array."
    )
    if feedback:
        prompt += f"\n\nIMPORTANT HUMAN FEEDBACK FOR REVISION:\n{feedback}\nPlease rewrite the replies adhering strictly to this feedback."

    user_msg = types.Content(role="user", parts=[types.Part(text=prompt)])

    batch_results = []
    raw_response = ""

    for event in runner.run(
        user_id="inbox_batch_runner",
        session_id=session.id,
        new_message=user_msg
    ):
        if hasattr(event, 'content') and event.content and event.content.parts:
            text = event.content.parts[0].text
            raw_response = text  # ADK yields cumulative text, so assignment is correct.

    def _extract_json_array(text: str) -> list | None:
        """Try to extract a JSON array from LLM text output."""
        import re
        # Strategy 1: regex extract [...] block
        match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        # Strategy 2: strip markdown fences
        cleaned = text.strip()
        for prefix in ('```json', '```'):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
        try:
            parsed = json.loads(cleaned.strip())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return None

    logger.info(f"[ADK BATCH] Raw LLM Output:\n{raw_response}")

    batch_results = _extract_json_array(raw_response) or []

    # Retry once with a corrective prompt if first attempt failed
    if not batch_results and raw_response.strip():
        logger.warning("[ADK BATCH] First attempt returned no valid JSON. Retrying with corrective prompt...")
        retry_msg = types.Content(
            role="user",
            parts=[types.Part(text=(
                "Your previous response was NOT valid JSON. It contained reasoning text instead of the JSON array.\n"
                "Please output ONLY the JSON array now. Start with '[' and end with ']'.\n"
                "Do NOT include any explanation, planning, or markdown. ONLY the JSON array."
            ))]
        )
        raw_retry = ""
        for event in runner.run(
            user_id="inbox_batch_runner",
            session_id=session.id,
            new_message=retry_msg
        ):
            if hasattr(event, 'content') and event.content and event.content.parts:
                raw_retry = event.content.parts[0].text

        logger.info(f"[ADK BATCH] Retry Raw LLM Output:\n{raw_retry}")
        batch_results = _extract_json_array(raw_retry) or []

    if batch_results:
        logger.info(f"[ADK BATCH] Successfully parsed {len(batch_results)} thread replies.")
    else:
        logger.error(f"[ADK BATCH] Failed to extract JSON after retry. Raw: {raw_response[:500]}...")

    return batch_results


def _sanitize_reply(text: str) -> str:
    """Remove LLM reasoning-leak lines from a generated reply.

    Lines starting with '**' (e.g. '**Crafting a warm reply**') and lines
    that are pure reasoning narration are stripped. Empty results raise a
    warning so callers know no usable reply was produced.

    Args:
        text: Raw reply text from the LLM.

    Returns:
        Cleaned reply string (may be empty if the entire output was reasoning).
    """
    import re

    if not text:
        return text

    reasoning_patterns = re.compile(
        r'^(\*\*.*\*\*'                    # **Any heading**
        r'|I need to\b'                    # "I need to ..."
        r'|I\'m (?:going to|working|thinking|attempting)\b'
        r'|Let me\b'                       # "Let me ..."
        r'|I should\b'                     # "I should ..."
        r'|I want to\b'                    # "I want to ..."
        r'|I\'ll\b'                        # "I'll ..."
        r'|Here is the reply'
        r'|Here\'s (?:my|the) reply'
        r')',
        re.IGNORECASE,
    )

    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if reasoning_patterns.match(stripped):
            logger.debug(f"[sanitize_reply] Stripped reasoning line: {stripped[:80]}")
            continue
        clean_lines.append(line)

    # Remove leading/trailing blank lines from the result
    cleaned = "\n".join(clean_lines).strip()
    if not cleaned and text.strip():
        logger.warning(
            "[sanitize_reply] Entire reply was reasoning leak — no clean text. "
            "Original (truncated): " + text[:120]
        )
    return cleaned
