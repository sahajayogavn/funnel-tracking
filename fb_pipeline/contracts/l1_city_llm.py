"""
Shared LLM-based city detection for seekers.
code:tool-citydetect-001:llm-detect

Replaces keyword-based detect_city() with LLM inference using
3 signals ordered by priority:
  1. User's confirmed registration message (city they gave)
  2. City the user lives in (from their own messages)
  3. Ad content the user interacted with (weakest signal)

Uses OpenAI-compatible API (base URL + key from env_manager).
"""
import json
import logging
import os
import time
import requests
import re
from fb_pipeline.contracts.l1_program_catalog import PROGRAM_CODES
from fb_pipeline.persistence.l4_llm_trace import start_call, end_call, span_attempt

logger = logging.getLogger("city_llm")

# code:tool-citydetect-001:llm-retry
LLM_MAX_RETRIES = 10
LLM_RETRY_BASE_SLEEP = 3.0


def _call_llm_with_retry(fn, label: str, max_retries: int = LLM_MAX_RETRIES,
                         base_sleep: float = LLM_RETRY_BASE_SLEEP):
    """Run ``fn()`` up to ``max_retries`` times with exponential backoff.

    Sleeps ``base_sleep`` seconds after the first failure and doubles the delay
    on every subsequent failure (3s, 6s, 12s, ...). Re-raises the last error
    once the retry budget is exhausted so callers keep their existing fallback.
    """
    delay = base_sleep
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with span_attempt(attempt):
                return fn()
        except Exception as exc:  # network, HTTP, JSON parse, malformed payload
            last_exc = exc
            if attempt >= max_retries:
                break
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.0fs",
                           label, attempt, max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
    logger.error("%s failed after %d attempts: %s", label, max_retries, last_exc)
    raise last_exc  # type: ignore[misc]


# code:tool-citydetect-001:output-budget
OUTPUT_TOKENS_PER_SEEKER = int(os.environ.get("CITY_LLM_TOKENS_PER_SEEKER", "110"))


def _batch_output_budget(n_seekers: int) -> int:
    """Hard cap on completion tokens so the model cannot ramble: one compact
    JSON row per seeker (~60-90 tokens with a Vietnamese name) plus headroom."""
    return max(1, n_seekers) * OUTPUT_TOKENS_PER_SEEKER + 200


# code:tool-citydetect-001:llm-stream
LLM_STREAM = os.environ.get("CITY_LLM_STREAM", "1") != "0"

def chat_completion_text(url: str, payload: dict, headers: dict, timeout: int) -> str:
    """POST an OpenAI-compatible chat completion and return the assistant text.

    Uses ``stream: true`` (SSE) by default. Retrospective [2026-09-16]: the
    upstream proxy sits behind Cloudflare, whose idle timeout returns HTTP 524
    on long non-streaming generations; with streaming, bytes flow as soon as the
    model starts emitting, so the connection never idles long enough to be cut.
    ``CITY_LLM_STREAM=0`` restores the plain request/response path.
    """
    messages_json = json.dumps(payload.get("messages", []), ensure_ascii=False)
    system_prompt = ""
    for m in payload.get("messages", []):
        if m.get("role") == "system":
            system_prompt = m.get("content", "")
            break
            
    call_id = start_call(
        agent_name="city_llm",
        model=payload.get("model", "unknown"),
        system_prompt=system_prompt,
        messages_json=messages_json,
        state_json="{}"
    )

    if not LLM_STREAM:
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            if call_id:
                end_call(call_id, response_text=text)
            return text
        except Exception as e:
            if call_id:
                end_call(call_id, error=str(e))
            raise

    stream_payload = dict(payload, stream=True)
    stream_headers = dict(headers, Accept="text/event-stream")
    # (connect, read) — read timeout is per-chunk, not per-request.
    resp = requests.post(url, json=stream_payload, headers=stream_headers,
                         timeout=(30, timeout), stream=True)
    try:
        resp.raise_for_status()
        ctype = str(resp.headers.get("Content-Type", "") or "")
        if "text/event-stream" not in ctype:
            # Server ignored stream=true; fall back to JSON body.
            return resp.json()["choices"][0]["message"]["content"]
        parts: list[str] = []
        # Decode bytes ourselves: without a charset in Content-Type, requests
        # assumes ISO-8859-1 for text/* and Vietnamese names turn into mojibake.
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in event.get("choices", []) or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    parts.append(content)
        text = "".join(parts)
        if not text:
            raise ValueError("LLM stream returned no content")
        if call_id:
            end_call(call_id, response_text=text)
        return text
    except Exception as e:
        if call_id:
            end_call(call_id, error=str(e))
        raise
    finally:
        try:
            resp.close()
        except Exception:
            pass

# code:tool-citydetect-001:known-cities
KNOWN_CITIES = [
    "Hà Nội",
    "TP. Hồ Chí Minh",
    "Đà Nẵng",
    "Huế",
    "Hội An",
    "Nghệ An",
    "Hải Phòng",
    "Online",
]

# code:tool-citydetect-001:prompt-template
SYSTEM_PROMPT = """You are a city classifier for a Vietnamese meditation center's CRM system.

Your task: determine which city a seeker (potential student) should be assigned to based on conversation signals.

## Known cities
{known_cities}

If the city does not match any known city, return "Unknown".

## Priority rules (MOST IMPORTANT → LEAST IMPORTANT)
1. **Registration confirmation message**: If the user explicitly confirmed which city's class they want to attend (e.g. "Mình đăng ký lớp Đà Nẵng", or the page sent them an address in a specific city), that city wins. This is the STRONGEST signal.
2. **City the user lives in**: If the user mentioned where they live (e.g. "Em ở HCM", "Mình ở gần Hội An"), use that.
3. **Ad content**: The ad the user clicked on may mention a city. This is the WEAKEST signal because one ad serves multiple cities.

## Important nuances
- Street names can exist in multiple cities. "Xô Viết Nghệ Tĩnh" exists in BOTH HCM and Đà Nẵng.
  Always look at the FULL address context, not just the street name.
- "Online" / "Zoom" / "trực tuyến" = the "Online" city.
- If signals conflict, always prefer the higher-priority signal.
- If no signal is clear enough, return "Unknown".

## Program detection
Choose `program_code` only when the **customer** expresses interest in, chooses,
or confirms attending that specific class, and the class can be unambiguously
identified by time/day/location. A Page reply may supply missing details only
when it directly answers that customer's specific choice. Ad content and a Page
reply alone are never evidence of the seeker's programme choice: one ad can
promote many classes in one city. Use exactly one of: {program_codes}.
If the customer only gives a city, asks generally about classes, or two classes
remain plausible, return null. Never infer a programme from city alone.

## Response format
Reply with ONLY a JSON object, no markdown fences, no explanation:
{{"city": "<city name>", "program_code": "<catalogue code or null>", "confidence": "high|medium|low", "reasoning": "<one-line explanation>"}}
"""

USER_PROMPT_TEMPLATE = """Classify the city for this seeker.

## Seeker: {thread_name}

## Signal 1 — Customer messages (HIGHEST priority)
{customer_messages}

## Signal 2 — Page replies to this user
{page_messages}

## Signal 3 — Ad content the user interacted with (LOWEST priority)
{ad_content}
"""


# `ad_posts` is shared by every seeker associated with an ad id. It must only
# contain creative text: Inbox DOM around a reply-to-ad can include another
# seeker's chat and must never become Signal 3 for a later classification.
_AD_CONTEXT_CONVERSATION_MARKERS = re.compile(
    r"\[Quoted Reply/Link\]|\b(?:replied to an ad|reply to your ad|"
    r"đã trả lời về một bài viết|sent by|message removed)\b",
    re.IGNORECASE,
)


def sanitize_ad_content(ad_content: object) -> str:
    """Keep an ad creative only when it contains no Inbox conversation data.

    Discarding suspect Signal 3 is safer than attempting to recover a fragment
    from a shared record and leaking or misclassifying another seeker's data.
    """
    if not isinstance(ad_content, str):
        return ""
    cleaned = ad_content.strip()
    if not cleaned or _AD_CONTEXT_CONVERSATION_MARKERS.search(cleaned):
        return ""
    return cleaned


def _build_prompt(thread_name: str, customer_messages: list[str],
                  page_messages: list[str], ad_content: str) -> str:
    """Build the user prompt from the 3 signals."""
    cust_text = "\n".join(customer_messages) if customer_messages else "(no customer messages)"
    page_text = "\n".join(page_messages) if page_messages else "(no page messages)"
    ad_text = ad_content.strip() if ad_content else "(no ad content)"

    return USER_PROMPT_TEMPLATE.format(
        thread_name=thread_name,
        customer_messages=cust_text,
        page_messages=page_text,
        ad_content=ad_text,
    )


def _parse_llm_response(raw: str) -> dict:
    """Parse LLM JSON response, handling common issues."""
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
        city = result.get("city", "Unknown")
        # Normalize city name
        if city not in KNOWN_CITIES and city != "Unknown":
            # Try fuzzy matching common variants
            city_lower = city.lower()
            for known in KNOWN_CITIES:
                if known.lower() in city_lower or city_lower in known.lower():
                    city = known
                    break
            else:
                city = "Unknown"
        return {
            "city": city,
            "program_code": result.get("program_code") if result.get("program_code") in PROGRAM_CODES else None,
            "confidence": result.get("confidence", "low"),
            "reasoning": result.get("reasoning", ""),
        }
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM response as JSON: {text[:200]}")
        # Try to extract city from free-text response
        for known in KNOWN_CITIES:
            if known in text:
                return {"city": known, "program_code": None, "confidence": "low", "reasoning": "Extracted from free-text response"}
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": f"Parse error: {text[:100]}"}


# code:tool-citydetect-001:llm-call
def detect_city_llm(thread_name: str, customer_messages: list[str],
                    page_messages: list[str], ad_content: str,
                    api_base: str, api_key: str, model: str,
                    timeout: int = 30) -> dict:
    """Detect city for ONE user using LLM inference.

    Args:
        thread_name: Name of the seeker (thread_name from users table).
        customer_messages: List of message content strings from the Customer.
        page_messages: List of message content strings from the Page.
        ad_content: Combined ad content text from ad_posts.
        api_base: OpenAI-compatible API base URL.
        api_key: API key.
        model: Model name (e.g. "gpt-5.4").
        timeout: Request timeout in seconds.

    Returns:
        dict with keys: city, confidence, reasoning
    """
    system = SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES))
    user_prompt = _build_prompt(thread_name, customer_messages, page_messages, ad_content)

    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 200,
    }

    def _do_request():
        raw_content = chat_completion_text(url, payload, headers, timeout)
        return _parse_llm_response(raw_content)

    try:
        return _call_llm_with_retry(_do_request, f"LLM city detect ({thread_name})")
    except requests.exceptions.Timeout:
        logger.error(f"LLM request timed out for {thread_name}")
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": "API timeout"}
    except requests.exceptions.RequestException as e:
        logger.error(f"LLM request failed for {thread_name}: {e}")
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": f"API error: {e}"}
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected LLM response structure for {thread_name}: {e}")
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": f"Response parse error: {e}"}

BATCH_SYSTEM_PROMPT = """You are a city classifier for a Vietnamese meditation center's CRM system.

Your task: determine which city each seeker (potential student) should be assigned to based on their conversation signals.

## Known cities
{known_cities}

If the city does not match any known city, return "Unknown".

## Priority rules (MOST IMPORTANT → LEAST IMPORTANT)
1. **Registration confirmation message**: If the user explicitly confirmed which city's class they want to attend (e.g. "Mình đăng ký lớp Đà Nẵng", or the page sent them an address in a specific city), that city wins. This is the STRONGEST signal.
2. **City the user lives in**: If the user mentioned where they live (e.g. "Em ở HCM", "Mình ở gần Hội An"), use that.
3. **Ad content**: The ad the user clicked on may mention a city. This is the WEAKEST signal because one ad serves multiple cities.

## Important nuances
- Street names can exist in multiple cities. "Xô Viết Nghệ Tĩnh" exists in BOTH HCM and Đà Nẵng.
  Always look at the FULL address context, not just the street name.
- "Online" / "Zoom" / "trực tuyến" = the "Online" city.
- If signals conflict, always prefer the higher-priority signal.
- If no signal is clear enough, return "Unknown".

## Program detection
Choose `program_code` only when the customer expresses interest in, chooses, or
confirms attending that specific class and time/day/location identifies it
exactly: {program_codes}. A Page reply can only complete a customer-selected
class; an ad or Page reply alone is never enough. Otherwise return null; never
guess from city alone.

## Response format
Reply with ONLY a JSON array containing one object for each seeker. Output MUST exactly match this format without markdown fences:
[
  {{"thread_name": "<exact name provided>", "city": "<city name>", "program_code": "<catalogue code or null>", "confidence": "high|medium|low", "reasoning": "<max 12 words>"}}
Keep the output compact: minified JSON on one line, no whitespace padding, no commentary, reasoning at most 12 words.
]
"""

# code:tool-citydetect-001:llm-batch-call
def detect_city_batch_llm(batch_payload: str,
                          api_base: str, api_key: str, model: str,
                          timeout: int = 300) -> list[dict]:
    """Detect city for multiple users in one request.

    Args:
        batch_payload: Formatted string containing multiple users' signals.
        api_base: OpenAI-compatible API base URL.
        api_key: API key.
        model: Model name.
        timeout: Request timeout.

    Returns:
        list of dicts with keys: thread_name, city, confidence, reasoning
    """
    system = BATCH_SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES))
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": batch_payload},
        ],
        "temperature": 0.1,
        "max_tokens": _batch_output_budget(batch_payload.count("## Seeker:")),
    }

    def _do_request():
        raw_content = chat_completion_text(url, payload, headers, timeout)

        # Parse JSON array
        text = raw_content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
            
        results = json.loads(text)
        if not isinstance(results, list):
            results = [results]
            
        # Normalize each city
        for res in results:
            city = res.get("city", "Unknown")
            if city not in KNOWN_CITIES and city != "Unknown":
                city_lower = city.lower()
                for known in KNOWN_CITIES:
                    if known.lower() in city_lower or city_lower in known.lower():
                        city = known
                        break
                else:
                    city = "Unknown"
            res["city"] = city
            res["program_code"] = res.get("program_code") if res.get("program_code") in PROGRAM_CODES else None
            
        return results

    try:
        return _call_llm_with_retry(_do_request, "LLM city/program batch detect")
    except requests.exceptions.Timeout:
        logger.error("LLM batch request timed out")
        return []
    except Exception as e:
        logger.error(f"LLM batch request failed: {e}")
        return []


VERIFY_BATCH_SYSTEM_PROMPT = """You are the independent verifier for a Vietnamese meditation CRM.
Review proposed city and programme classifications against the supplied customer messages, Page replies, and ad context.

Valid cities: {known_cities}. Valid programme codes: {program_codes}.

Programme rule: assign a programme only if the CUSTOMER expresses interest in, chooses, or confirms that specific class. Ads and Page replies alone never prove programme selection. If uncertain, use null.
City rule: prefer an explicit customer registration/location over Page replies; ads are weakest.

Return ONLY a JSON array, one object for every supplied seeker:
{{"thread_name":"exact name","city":"valid city or Unknown","program_code":"valid code or null","verified":true,"proof":"shortest quote proving it, max 15 words"}}
Keep the output compact: minified JSON on one line, no commentary, proof at most 15 words (empty string when not verified).
Set verified=false only when the evidence is insufficient; then city must be Unknown and program_code null."""


def verify_city_program_batch_llm(batch_payload: str, proposed_results: list[dict],
                                  api_base: str, api_key: str, model: str,
                                  timeout: int = 300) -> list[dict]:
    """Second, independent LLM pass for one batch of City/Programme decisions."""
    proposed = json.dumps(proposed_results, ensure_ascii=False)
    user_content = f"## Conversation signals\n{batch_payload}\n\n## Proposed classifications\n{proposed}"
    payload = {
        "model": model.removeprefix("openai/"),
        "messages": [
            {"role": "system", "content": VERIFY_BATCH_SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES))},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": _batch_output_budget(len(proposed_results)),
    }
    def _do_request():
        text = chat_completion_text(
            f"{api_base.rstrip('/')}/chat/completions", payload,
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, timeout,
        ).strip()
        if text.startswith("```"):
            text = "\n".join(line for line in text.split("\n") if not line.strip().startswith("```"))
        rows = json.loads(text)
        if not isinstance(rows, list):
            rows = [rows]
        normalized = []
        for row in rows:
            city = row.get("city", "Unknown")
            if city not in KNOWN_CITIES:
                city = "Unknown"
            program_code = row.get("program_code") if row.get("program_code") in PROGRAM_CODES else None
            proof = str(row.get("proof", "")).strip()[:500]
            verified = bool(row.get("verified", False)) and bool(proof)
            if not verified:
                city, program_code = "Unknown", None
            normalized.append({
                "thread_name": row.get("thread_name", ""), "city": city,
                "program_code": program_code, "verified": verified,
                "proof": proof,
            })
        return normalized

    try:
        return _call_llm_with_retry(_do_request, "LLM city/program batch verify")
    except Exception as exc:
        logger.error("LLM city/program verification failed: %s", exc)
        return []


# code:tool-citydetect-001:db-gather
def gather_signals_for_user(conn, thread_id: str) -> dict:
    """Gather all 3 classification signals for a user from the database.

    Args:
        conn: sqlite3 connection to frankensqlite.db
        thread_id: The user's thread_id

    Returns:
        dict with keys: thread_name, customer_messages, page_messages, ad_content
    """
    cursor = conn.cursor()

    # Get thread_name
    cursor.execute("SELECT thread_name FROM users WHERE thread_id = ?", (thread_id,))
    row = cursor.fetchone()
    thread_name = row["thread_name"] if row else "Unknown"

    # Get customer messages (Signal 1+2: registration confirmations + living city)
    cursor.execute(
        "SELECT content FROM messages WHERE thread_id = ? AND sender = 'Customer' ORDER BY id ASC",
        (thread_id,)
    )
    customer_messages = [r["content"] for r in cursor.fetchall()]

    # Get page messages (Signal 2 supplement: page replies often contain class addresses)
    cursor.execute(
        "SELECT content FROM messages WHERE thread_id = ? AND sender = 'Page' ORDER BY id ASC",
        (thread_id,)
    )
    page_messages = [r["content"] for r in cursor.fetchall()]

    # Get ad content (Signal 3: weakest, from ad_posts via user_ad_ids)
    cursor.execute("""
        SELECT ap.ad_content FROM ad_posts ap
        JOIN user_ad_ids ua ON ap.ad_id = ua.ad_id
        WHERE ua.thread_id = ?
    """, (thread_id,))
    ad_parts = [
        cleaned
        for row in cursor.fetchall()
        if (cleaned := sanitize_ad_content(row["ad_content"]))
    ]
    ad_content = "\n---\n".join(ad_parts) if ad_parts else ""

    return {
        "thread_name": thread_name,
        "customer_messages": customer_messages,
        "page_messages": page_messages,
        "ad_content": ad_content,
    }


__all__ = [
    "KNOWN_CITIES",
    "detect_city_llm",
    "detect_city_batch_llm",
    "verify_city_program_batch_llm",
    "sanitize_ad_content",
    "gather_signals_for_user",
]
