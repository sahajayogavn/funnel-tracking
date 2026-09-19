"""
Shared LLM-based city detection for seekers.
code:tool-citydetect-001:llm-detect

Replaces keyword-based detect_city() with LLM inference using
3 signals ordered by priority:
  1. User's confirmed registration message (city they gave)
  2. City the user lives in (from their own messages)
  3. Ad content the user interacted with (weakest signal)

Uses the shared native Gemini provider configuration.
"""
import json
import logging
import os
import time
import requests
import re
from fb_pipeline.contracts.l1_program_catalog import PROGRAM_CODES
from fb_pipeline.persistence.l4_llm_trace import end_call, span, start_call, span_attempt
from tools.l5_llm_provider import GEMINI_API_BASE, IncompleteGeminiResponse, generate_text

logger = logging.getLogger("city_llm")

# code:tool-citydetect-001:llm-retry
LLM_MAX_RETRIES = 10
LLM_RETRY_BASE_SLEEP = 3.0


def _retryable_llm_exception(exc: Exception) -> bool:
    """Do not spend an epoch retrying a credential/model configuration error.

    A 401/403/404 cannot improve inside the same process.  The outer
    ``mas-classify`` loop is the retry boundary, so it can pick up corrected
    `.env` configuration on its next epoch.
    """
    if isinstance(exc, IncompleteGeminiResponse):
        return False
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code not in {400, 401, 403, 404, 422}
    return True


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
            if attempt >= max_retries or not _retryable_llm_exception(exc):
                if not _retryable_llm_exception(exc):
                    logger.error("%s failed with non-retryable configuration/API error: %s", label, exc)
                break
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.0fs",
                           label, attempt, max_retries, exc, delay)
            time.sleep(delay)
            delay *= 2
    logger.error("%s failed after %d attempts: %s", label, max_retries, last_exc)
    raise last_exc  # type: ignore[misc]


# code:tool-citydetect-001:output-budget
# Gemini 3 reserves part of this shared cap for thinking.  400 was too small:
# valid responses were cut before program/name/phone, causing a city-only
# fallback.  Keep this configurable while using a safe production default.
SINGLE_SEEKER_OUTPUT_TOKENS = int(os.environ.get("CITY_LLM_SINGLE_OUTPUT_TOKENS", "1024"))
OUTPUT_TOKENS_PER_SEEKER = int(os.environ.get("CITY_LLM_TOKENS_PER_SEEKER", "110"))


def _batch_output_budget(n_seekers: int) -> int:
    """Hard cap on completion tokens so the model cannot ramble: one compact
    JSON row per seeker (~60-90 tokens with a Vietnamese name) plus headroom."""
    return max(1, n_seekers) * OUTPUT_TOKENS_PER_SEEKER + 200


def gemini_completion_text(config: dict, system_prompt: str, user_prompt: str,
                           temperature: float, max_tokens: int, timeout: int) -> str:
    """Call the common Gemini transport while preserving City LLM tracing."""
    model = config["model"].removeprefix("models/")
    endpoint = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    state_json = json.dumps(
        {
            "endpoint": endpoint,
            "request": {
                "provider": config.get("provider"), "model": model,
                "temperature": temperature, "max_tokens": max_tokens,
            },
        },
        ensure_ascii=False,
    )
    call_id = start_call(
        agent_name="city_llm", model=model, system_prompt=system_prompt,
        messages_json=json.dumps([{"role": "user", "content": user_prompt}], ensure_ascii=False),
        state_json=state_json,
    )
    try:
        text, usage = generate_text(
            config=config, system_prompt=system_prompt, user_prompt=user_prompt,
            temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        )
        end_call(
            call_id, response_text=text,
            response_json=json.dumps({"usage": usage}, ensure_ascii=False),
            tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
        )
        return text
    except Exception as exc:
        end_call(call_id, error=str(exc))
        raise

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

# A programme identifies the team that owns an online class. ``Online`` stays
# valid only when the evidence cannot distinguish an owning city.
PROGRAM_CITY = {
    "20h30-Online-HCM": "TP. Hồ Chí Minh", "16h-CN-Đào Duy Anh-HCM": "TP. Hồ Chí Minh",
    "14h-CN-Đào Duy Anh-HCM": "TP. Hồ Chí Minh", "20h-T3-Hoàng Quốc Việt-HN": "Hà Nội",
    "14h30-CN-Vương Thừa Vũ-HN": "Hà Nội", "15h30-CN-Vương Thừa Vũ-HN": "Hà Nội",
    "21h-T3-T5-T7-Online-HN": "Hà Nội", "8h30-CN-Xô Viết Nghệ Tĩnh-ĐN": "Đà Nẵng",
    "8h30-CN-Trần Nhân Tông-HA": "Hội An",
}

PROGRAM_CATALOGUE = """- 20h30-Online-HCM | TP. Hồ Chí Minh | Online Zoom | 20:30–21:30, T2–T6
- 16h-CN-Đào Duy Anh-HCM | TP. Hồ Chí Minh | 158 Đào Duy Anh | CN 16:00–17:30
- 14h-CN-Đào Duy Anh-HCM | TP. Hồ Chí Minh | 158 Đào Duy Anh | CN 14:00–16:00
- 20h-T3-Hoàng Quốc Việt-HN | Hà Nội | 72 ngõ 106 Hoàng Quốc Việt | T3 20:00–21:00
- 14h30-CN-Vương Thừa Vũ-HN | Hà Nội | 40 Vương Thừa Vũ | CN 14:30–15:30
- 15h30-CN-Vương Thừa Vũ-HN | Hà Nội | 40 Vương Thừa Vũ | CN 15:30–17:00
- 21h-T3-T5-T7-Online-HN | Hà Nội | Online Zoom | T3/T5/T7 21:00–21:30
- 8h30-CN-Xô Viết Nghệ Tĩnh-ĐN | Đà Nẵng | 2 Xô Viết Nghệ Tĩnh | CN 08:30–10:00
- 8h30-CN-Trần Nhân Tông-HA | Hội An | 121 Trần Nhân Tông | CN 08:30–10:00"""

# code:tool-citydetect-001:prompt-template
SYSTEM_PROMPT = """You are a city classifier for a Vietnamese meditation center's CRM system.

Your task: determine which city a seeker (potential student) should be assigned to based on conversation signals.

## Known cities
{known_cities}

If the city does not match any known city, return "Unknown".

## Priority rules (MOST IMPORTANT → LEAST IMPORTANT)
1. **Registration confirmation message**: If the user explicitly confirmed which city's class they want to attend (e.g. "Mình đăng ký lớp Đà Nẵng", or the page sent them an address in a specific city), that city wins. This is the STRONGEST signal.
2. **City the user lives in**: If the user mentioned where they live (e.g. "Em ở HCM", "Mình ở gần Hội An"), use that.
3. **Matched ad/post content**: The ad or post the user actually interacted with may identify the promoted class/city. It is weaker than an explicit customer choice, but stronger than a Page message that merely asks a question.

## Important nuances
- Street names can exist in multiple cities. "Xô Viết Nghệ Tĩnh" exists in BOTH HCM and Đà Nẵng.
  Always look at the FULL address context, not just the street name.
- A Page question (for example, "bạn quan tâm lớp ở Hà Nội phải không?") is NOT confirmation and must not override the seeker's prior or subsequent choice.
- If a programme matches the catalogue, its owning city is mandatory: 20h30-Online-HCM => TP. Hồ Chí Minh; 21h-T3-T5-T7-Online-HN => Hà Nội.
- Return "Online" only when the seeker chose an online class but the conversation, matched ad/post, and catalogue cannot distinguish its owning city.
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

## Contact extraction
Also extract the seeker's registration contact details from **Customer messages
only**. Never take a name or phone number from Page messages, ads, quoted text,
or the Facebook thread name.
- `full_name`: return a name only when the customer explicitly supplies it as
  registration information; otherwise null. Preserve Vietnamese diacritics.
- `phone`: return a phone only when the customer supplied it. Correct obvious
  digit look-alikes used in a phone number (`o`/`O` → `0`, spaces, dots and
  dashes). Return Vietnamese numbers in domestic digits, e.g. `0904069868`.
  Do not invent missing digits; return null if the number remains ambiguous.

## Current class catalogue
{program_catalogue}

## Response format
Reply with ONLY a JSON object, no markdown fences, no explanation:
{{"city":"<city name>","program_code":"<catalogue code or null>","full_name":"<customer-provided name or null>","phone":"<customer-provided normalized phone or null>","confidence":"high|medium|low"}}

Do not include `proof`, `reasoning`, markdown, or any extra keys. The JSON
must be complete and compact.
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


# `ad_posts` is shared by every seeker associated with an ad id.  It must
# therefore contain only the creative, never the Inbox DOM surrounding a
# reply-to-ad event.  Those DOM excerpts include other seekers' messages and
# can otherwise be sent to an unrelated person's classifier as Signal 3.
_AD_CONTEXT_CONVERSATION_MARKERS = re.compile(
    r"\[Quoted Reply/Link\]|\b(?:replied to an ad|reply to your ad|"
    r"đã trả lời về một bài viết|sent by|message removed)\b",
    re.IGNORECASE,
)


def sanitize_ad_content(ad_content: object) -> str:
    """Return an ad creative only when it is free of Inbox conversation data.

    We deliberately discard a suspect value instead of attempting to recover
    a fragment: a shared ad record with cross-thread chat is unsafe evidence,
    and a missing lowest-priority signal is preferable to a false city.
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

    def normalize_result(result: dict) -> dict:
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
        program_code = result.get("program_code") if result.get("program_code") in PROGRAM_CODES else None
        if program_code:
            city = PROGRAM_CITY[program_code]
        return {
            "city": city,
            "program_code": program_code,
            "full_name": _normalize_full_name(result.get("full_name")),
            "phone": _normalize_vietnamese_phone(result.get("phone")),
            "confidence": result.get("confidence", "low"),
            "proof": str(result.get("proof", "")).strip()[:500],
            "reasoning": result.get("reasoning", ""),
        }

    try:
        return normalize_result(json.loads(text))
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM response as JSON: {text[:200]}")
        # Gemini can occasionally stop while beginning an optional key. Recover
        # only completed scalar fields from a JSON prefix; each recovered value
        # still goes through the same city/catalogue/name/phone validators.
        def completed_json_field(key: str):
            match = re.search(rf'"{re.escape(key)}"\s*:\s*(null|"(?:\\.|[^"\\])*")', text)
            if not match:
                return None
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None

        partial = {key: completed_json_field(key) for key in (
            "city", "program_code", "full_name", "phone", "confidence",
        )}
        if any(partial[key] is not None for key in ("program_code", "full_name", "phone")):
            recovered = normalize_result(partial)
            recovered["proof"] = "Recovered from a complete JSON prefix."
            recovered["reasoning"] = "Partial JSON response recovered."
            logger.warning("Recovered City/Program/Name/Phone from truncated JSON response.")
            return recovered
        # Try to extract city from free-text response
        for known in KNOWN_CITIES:
            if known in text:
                return {"city": known, "program_code": None, "full_name": None, "phone": None, "confidence": "low", "proof": "", "reasoning": "Extracted from free-text response"}
        return {"city": "Unknown", "program_code": None, "full_name": None, "phone": None, "confidence": "low", "proof": "", "reasoning": f"Parse error: {text[:100]}"}


def _normalize_full_name(value: object) -> str | None:
    """Accept only a compact, human-looking name returned by the LLM."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.split()).strip(" ,.:;-")
    if not 2 <= len(name) <= 100 or any(char.isdigit() for char in name):
        return None
    # Registration names are commonly typed in lower/upper case inconsistently
    # (for example, "Bùi thị Thúy").  Use conventional title casing for the
    # stored seeker label while retaining every Vietnamese character.
    return " ".join(word[:1].upper() + word[1:].lower() for word in name.split()) or None


def _normalize_vietnamese_phone(value: object) -> str | None:
    """Normalize a customer-provided Vietnamese phone, rejecting guesses.

    The LLM is allowed to repair an obvious letter-O typo but this final gate
    ensures the database never receives prose, an extension, or a Page number.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("O", "0").replace("o", "0")
    cleaned = re.sub(r"[.\s()\-]", "", cleaned)
    if cleaned.startswith("+84"):
        cleaned = "0" + cleaned[3:]
    elif cleaned.startswith("84") and len(cleaned) in (11, 12):
        cleaned = "0" + cleaned[2:]
    return cleaned if re.fullmatch(r"0\d{9,10}", cleaned) else None


# code:tool-citydetect-001:llm-call
def detect_city_llm(thread_name: str, customer_messages: list[str],
                    page_messages: list[str], ad_content: str,
                    api_base: str = "", api_key: str = "", model: str = "",
                    timeout: int = 30, subject_id: str | None = None,
                    page_id: str | None = None, trigger: str = "unknown",
                    trace_id: str | None = None, llm_config: dict | None = None) -> dict:
    """Detect city for ONE user using LLM inference.

    Args:
        thread_name: Name of the seeker (thread_name from users table).
        customer_messages: List of message content strings from the Customer.
        page_messages: List of message content strings from the Page.
        ad_content: Combined ad content text from ad_posts.
        llm_config: shared Gemini-only provider config.  The positional
            ``api_*`` arguments are retained only for compatibility with old
            callers; they are treated as a Gemini key/model and ``api_base``
            is ignored.
        timeout: Request timeout in seconds.

    Returns:
        dict with keys: city, confidence, reasoning
    """
    system = SYSTEM_PROMPT.format(
        known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES),
        program_catalogue=PROGRAM_CATALOGUE,
    )
    user_prompt = _build_prompt(thread_name, customer_messages, page_messages, ad_content)

    config = llm_config or {"provider": "google", "api_key": api_key, "model": model}

    def _do_request():
        raw_content = gemini_completion_text(
            config, system, user_prompt, 0.1, SINGLE_SEEKER_OUTPUT_TOKENS, timeout,
        )
        return _parse_llm_response(raw_content)

    try:
        with span(
            trigger=trigger,
            route="classify_detect",
            page_id=page_id,
            subject=("user", str(subject_id or thread_name or "unknown"), thread_name or subject_id or "Unknown"),
            dry_run=True,
            trace_id=trace_id,
        ):
            return _call_llm_with_retry(_do_request, f"LLM city detect ({thread_name})")
    except requests.exceptions.Timeout:
        logger.error(f"LLM request timed out for {thread_name}")
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": "API timeout"}
    except requests.exceptions.RequestException as e:
        logger.error(f"LLM request failed for {thread_name}: {e}")
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": f"API error: {e}"}
    except IncompleteGeminiResponse as e:
        # Do not derive a city from a partial JSON response.  The caller marks
        # this retryable and preserves any prior classification values.
        logger.error("LLM response incomplete for %s: %s", thread_name, e)
        return {"city": "Unknown", "program_code": None, "confidence": "low", "reasoning": f"Response parse error: {e}"}
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
                          timeout: int = 300, subject_id: str | None = None,
                          subject_label: str | None = None, page_id: str | None = None,
                          trigger: str = "unknown", trace_id: str | None = None,
                          llm_config: dict | None = None) -> list[dict]:
    """Detect city for multiple users in one request.

    Args:
        batch_payload: Formatted string containing multiple users' signals.
        api_base: Deprecated compatibility argument; ignored.
        api_key: Gemini API key when ``llm_config`` is omitted.
        model: Gemini model name when ``llm_config`` is omitted.
        timeout: Request timeout.

    Returns:
        list of dicts with keys: thread_name, city, confidence, reasoning
    """
    system = BATCH_SYSTEM_PROMPT.format(known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES))
    config = llm_config or {"provider": "google", "api_key": api_key, "model": model}

    def _do_request():
        raw_content = gemini_completion_text(
            config, system, batch_payload, 0.1,
            _batch_output_budget(batch_payload.count("## Seeker:")), timeout,
        )

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
        with span(
            trigger=trigger,
            route="classify_detect",
            page_id=page_id,
            subject=("batch", str(subject_id or "batch:unknown"), subject_label or subject_id or "City/program detect batch"),
            dry_run=True,
            trace_id=trace_id,
        ):
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
                                  timeout: int = 300, subject_id: str | None = None,
                                  subject_label: str | None = None, page_id: str | None = None,
                                  trigger: str = "unknown", trace_id: str | None = None,
                                  llm_config: dict | None = None) -> list[dict]:
    """Second, independent LLM pass for one batch of City/Programme decisions."""
    proposed = json.dumps(proposed_results, ensure_ascii=False)
    user_content = f"## Conversation signals\n{batch_payload}\n\n## Proposed classifications\n{proposed}"
    system = VERIFY_BATCH_SYSTEM_PROMPT.format(
        known_cities=", ".join(KNOWN_CITIES), program_codes=", ".join(PROGRAM_CODES),
    )
    config = llm_config or {"provider": "google", "api_key": api_key, "model": model}
    def _do_request():
        text = gemini_completion_text(
            config, system, user_content, 0, _batch_output_budget(len(proposed_results)), timeout,
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
        with span(
            trigger=trigger,
            route="classify_verify",
            page_id=page_id,
            subject=("batch", str(subject_id or "batch:unknown"), subject_label or subject_id or "City/program verify batch"),
            dry_run=True,
            trace_id=trace_id,
        ):
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
    "gather_signals_for_user",
    "sanitize_ad_content",
]
