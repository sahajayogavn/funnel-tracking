"""
Root agent definition for the Sahaja Yoga Inbox MAS.
code:agent-mas-001:root-agent

Defines a SequentialAgent pipeline:
  1. Classifier — determines intent, language, sentiment
  2. Responder — generates a contextual reply

Uses OpenAI-compatible LLM via LiteLLM (ADK built-in support).

Run with:
    adk run adk_agents/
    adk web adk_agents/
"""
import os

from google.adk.agents import LlmAgent, SequentialAgent

from .tools.seeker_tools import lookup_seeker, get_thread_messages

# --- Model Configuration ---
# ADK uses LiteLLM under the hood. For OpenAI-compatible endpoints,
# prefix the model name with "openai/" and set env vars.
MODEL_NAME = os.environ.get("ADK_MODEL", "openai/gpt-5.4")

# --- Sub-Agent: Classifier ---
# code:agent-mas-001:classifier
classifier = LlmAgent(
    name="MessageClassifier",
    model=MODEL_NAME,
    instruction="""You are a message classifier for a Sahaja Yoga meditation center's Facebook inbox.

Analyze the incoming message and determine:
1. **intent**: One of: greeting, question, registration, follow_up, complaint, thanks, spam
2. **language**: "vi" for Vietnamese, "en" for English
3. **sentiment**: positive, neutral, or negative
4. **urgency**: low, medium, or high

The conversation thread is:
{thread_messages}

Seeker profile (if available):
{seeker_context}

Respond with a brief JSON-like summary:
Intent: <intent>
Language: <language>
Sentiment: <sentiment>
Urgency: <urgency>
Summary: <one-line summary of what the seeker wants>""",
    output_key="classification",
)

# --- Sub-Agent: Responder ---
# code:agent-mas-001:responder
responder = LlmAgent(
    name="Responder",
    model=MODEL_NAME,
    instruction="""You are a warm, compassionate guide at a Sahaja Yoga meditation center.
You reply to Facebook inbox messages on behalf of the center.

## Your Classification of This Message
{classification}

## The Conversation Thread
{thread_messages}

## Seeker Profile
{seeker_context}

## Communication Guidelines
1. Always be warm, welcoming, and genuine
2. Reply in the SAME LANGUAGE the seeker used (Vietnamese or English)
3. If they ask about meditation classes, share that classes are FREE and held regularly
4. If they want to register, ask for their name, phone number, and preferred city
5. If they express concerns, be patient and compassionate
6. NEVER be pushy, commercial, or salesy — meditation is always free
7. Keep replies concise (2-4 sentences max)
8. If uncertain about the question, politely ask for clarification

## Response Instructions
Write ONLY the reply message text. No metadata, no labels, no JSON.
Just the natural message you would send to this person.""",
    output_key="reply_text",
)

# --- Root Pipeline ---
# code:agent-mas-001:pipeline
root_agent = SequentialAgent(
    name="SahajaYogaInboxMAS",
    description="Multi-agent pipeline for handling Facebook inbox messages",
    sub_agents=[classifier, responder],
)
