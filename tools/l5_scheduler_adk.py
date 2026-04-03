import json
import asyncio

# code:tool-scheduler-001:reactor-adk
# code:tool-scheduler-001:warmup-composer-adk
# code:tool-scheduler-001:event-advertiser-adk
def _run_adk_route(agent, app_name: str, user_id: str, state: dict, prompt: str) -> list[dict]:
    """Run a single ADK route agent and collect text events."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )
    session = asyncio.run(
        session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
        )
    )

    user_msg = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    events = []
    for event in runner.run(user_id=user_id, session_id=session.id, new_message=user_msg):
        if hasattr(event, "content") and event.content and event.content.parts:
            text = event.content.parts[0].text
            if text:
                events.append({
                    "author": getattr(event, "author", ""),
                    "text": text.strip(),
                })
    return events


def run_adk_reactor(item: dict, dry_run: bool = True) -> str:
    """Run the Reactor ADK agent for a reaction decision."""
    from adk_agents.agent import reactor

    _ = dry_run
    state = {
        "reaction_content": item.get("content") or "",
        "reaction_sender": json.dumps(
            {
                "sender": item.get("sender"),
                "item_type": item.get("item_type"),
                "thread_id": item.get("thread_id"),
                "post_id": item.get("post_id"),
                "thread_name": item.get("thread_name"),
                "timestamp": item.get("timestamp"),
            },
            ensure_ascii=False,
            indent=2,
        ),
    }
    events = _run_adk_route(
        agent=reactor,
        app_name="sahajayoga_reactor",
        user_id="scheduler_reactor",
        state=state,
        prompt="Choose the best Facebook reaction using the provided session state.",
    )
    valid_reactions = {"like", "love", "care", "haha", "wow", "sad"}
    for event in reversed(events):
        reaction = (event.get("text") or "").strip().lower()
        if reaction in valid_reactions:
            return reaction
    return ""


def run_adk_warmup_composer(seeker: dict, strategy: dict, knowledge_context: str, dry_run: bool = True, feedback: str = None) -> str:
    """Run the WarmUpComposer ADK agent for a warm-up message."""
    from adk_agents.agent import warmup_composer

    _ = dry_run
    seeker_context = json.dumps(seeker, ensure_ascii=False, indent=2)
    warmup_brief = json.dumps(
        {
            "seeker_context": seeker,
            "strategy_type": strategy.get("type"),
            "cool_step": strategy.get("cool_step"),
            "knowledge_context": knowledge_context,
        },
        ensure_ascii=False,
        indent=2,
    )
    prompt = "Compose a warm-up message using the provided session state."
    if feedback:
        prompt += f"\\n\\nIMPORTANT HUMAN FEEDBACK FOR REVISION:\\n{feedback}\\nPlease rewrite the message adhering strictly to this feedback."
    events = _run_adk_route(
        agent=warmup_composer,
        app_name="sahajayoga_warmup",
        user_id="scheduler_warmup",
        state={
            "seeker_context": seeker_context,
            "strategy_type": strategy.get("type") or "",
            "cool_step": strategy.get("cool_step") or "",
            "knowledge_context": knowledge_context,
            "warmup_brief": warmup_brief,
        },
        prompt=prompt,
    )
    for event in reversed(events):
        message_text = (event.get("text") or "").strip()
        if message_text:
            return message_text
    return ""


def run_adk_event_advertiser(event: dict, seeker: dict, knowledge_context: str, dry_run: bool = True, feedback: str = None) -> str:
    """Run the EventAdvertiser ADK agent for an event notification."""
    from adk_agents.agent import event_advertiser

    _ = dry_run
    event_details = json.dumps({**event, "knowledge_context": knowledge_context}, ensure_ascii=False, indent=2)
    seeker_context = json.dumps(seeker, ensure_ascii=False, indent=2)
    prompt = "Compose an event notification using the provided session state."
    if feedback:
        prompt += f"\\n\\nIMPORTANT HUMAN FEEDBACK FOR REVISION:\\n{feedback}\\nPlease rewrite the message adhering strictly to this feedback."
    events = _run_adk_route(
        agent=event_advertiser,
        app_name="sahajayoga_event",
        user_id="scheduler_event",
        state={
            "event_details": event_details,
            "seeker_context": seeker_context,
        },
        prompt=prompt,
    )
    for event_output in reversed(events):
        message_text = (event_output.get("text") or "").strip()
        if message_text:
            return message_text
    return ""
