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
import requests

logger = logging.getLogger("city_llm")

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

## Response format
Reply with ONLY a JSON object, no markdown fences, no explanation:
{{"city": "<city name>", "confidence": "high|medium|low", "reasoning": "<one-line explanation>"}}
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
            "confidence": result.get("confidence", "low"),
            "reasoning": result.get("reasoning", ""),
        }
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM response as JSON: {text[:200]}")
        # Try to extract city from free-text response
        for known in KNOWN_CITIES:
            if known in text:
                return {"city": known, "confidence": "low", "reasoning": "Extracted from free-text response"}
        return {"city": "Unknown", "confidence": "low", "reasoning": f"Parse error: {text[:100]}"}


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
    system = SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES))
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

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        raw_content = data["choices"][0]["message"]["content"]
        return _parse_llm_response(raw_content)
    except requests.exceptions.Timeout:
        logger.error(f"LLM request timed out for {thread_name}")
        return {"city": "Unknown", "confidence": "low", "reasoning": "API timeout"}
    except requests.exceptions.RequestException as e:
        logger.error(f"LLM request failed for {thread_name}: {e}")
        return {"city": "Unknown", "confidence": "low", "reasoning": f"API error: {e}"}
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected LLM response structure for {thread_name}: {e}")
        return {"city": "Unknown", "confidence": "low", "reasoning": f"Response parse error: {e}"}

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

## Response format
Reply with ONLY a JSON array containing one object for each seeker. Output MUST exactly match this format without markdown fences:
[
  {{"thread_name": "<exact name provided>", "city": "<city name>", "confidence": "high|medium|low", "reasoning": "<one-line explanation>"}}
]
"""

# code:tool-citydetect-001:llm-batch-call
def detect_city_batch_llm(batch_payload: str,
                          api_base: str, api_key: str, model: str,
                          timeout: int = 90) -> list[dict]:
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
    system = BATCH_SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES))
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
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        raw_content = data["choices"][0]["message"]["content"]
        
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
            
        return results
    except requests.exceptions.Timeout:
        logger.error("LLM batch request timed out")
        return []
    except Exception as e:
        logger.error(f"LLM batch request failed: {e}")
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
    ad_parts = [r["ad_content"] for r in cursor.fetchall() if r["ad_content"]]
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
    "gather_signals_for_user",
]
