"""Provider configuration and transport for MAS LLM work.

The project `.env` is the source of truth.  This deployment intentionally
uses the selected provider from the encoded project `.env`; callers must not
inherit a stale provider endpoint or key from the shell.
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-5.6-terra"


class IncompleteGeminiResponse(RuntimeError):
    """Gemini stopped before producing a complete structured response."""


def get_llm_config() -> dict[str, str]:
    """Load the active provider from the encoded project `.env`."""
    from tools.env_manager import load_credentials

    credentials = load_credentials(prefer_environment=False)
    provider = (credentials.get("LLM_PROVIDER") or "google").strip().lower()
    if provider in {"openai", "openai-compatible", "openai_compatible"}:
        api_key = (credentials.get("OPENAI_COMPATIBLE_KEY") or "").strip()
        api_base = (credentials.get("OPENAI_COMPATIBLE_URL") or "").strip().rstrip("/")
        models = (credentials.get("OPENAI_COMPATIBLE_MODELS") or DEFAULT_OPENAI_COMPATIBLE_MODEL).strip()
        model = models.split(",", 1)[0].strip()
        if not api_key or not api_base or not model:
            raise RuntimeError("OpenAI-compatible credentials missing: configure OPENAI_COMPATIBLE_URL, OPENAI_COMPATIBLE_KEY, and OPENAI_COMPATIBLE_MODELS.")
        return {"provider": "openai-compatible", "api_key": api_key, "api_base": api_base, "model": model}
    if provider not in {"google", "gemini"}:
        raise RuntimeError(
            "Unsupported LLM_PROVIDER. Use google or openai-compatible."
        )

    api_key = (credentials.get("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Gemini credentials missing: set GOOGLE_API_KEY in the project .env.")
    model = (credentials.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
    model = model.removeprefix("google/").removeprefix("models/")
    if not model:
        raise RuntimeError("Gemini model missing: set GEMINI_MODEL in the project .env.")
    return {"provider": "google", "api_key": api_key, "model": model}


def setup_llm_env() -> dict[str, str]:
    """Make Google ADK use the selected project `.env` configuration.

    Removing legacy variables is deliberate: a process launched from an old
    shell must not silently route an MAS call to a different provider.
    """
    config = get_llm_config()
    if config["provider"] == "google":
        os.environ["GOOGLE_API_KEY"] = config["api_key"]
        os.environ["ADK_MODEL"] = config["model"]
        os.environ.pop("OPENAI_API_BASE", None)
        os.environ.pop("OPENAI_API_KEY", None)
    else:
        os.environ["OPENAI_API_KEY"] = config["api_key"]
        os.environ["OPENAI_API_BASE"] = config["api_base"]
        os.environ["ADK_MODEL"] = f"openai/{config['model']}"
        os.environ.pop("GOOGLE_API_KEY", None)
    return config


def generate_text(*, config: dict[str, str], system_prompt: str, user_prompt: str,
                  temperature: float, max_tokens: int, timeout: int) -> tuple[str, dict[str, Any]]:
    """Call the selected provider and return text plus safe usage.

    The API key goes in ``x-goog-api-key`` rather than the URL so trace logs
    can store the endpoint without exposing a credential.
    """
    if config.get("provider") == "openai-compatible":
        response = requests.post(
            f"{config['api_base']}/chat/completions",
            json={"model": config["model"], "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}],
                "temperature": temperature, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {config['api_key']}"},
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        text = str((body.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        if not text:
            raise ValueError("OpenAI-compatible response contained no text choice")
        usage = body.get("usage") or {}
        return text, {"prompt_tokens": usage.get("prompt_tokens"),
                      "completion_tokens": usage.get("completion_tokens"),
                      "total_tokens": usage.get("total_tokens"),
                      "finish_reason": (body.get("choices") or [{}])[0].get("finish_reason")}
    if config.get("provider") != "google":
        raise RuntimeError("Unsupported MAS LLM provider.")
    model = config["model"].removeprefix("models/")
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            # Gemini 3 counts thinking tokens against maxOutputTokens.  City,
            # programme and contact extraction is a small structured task, so
            # LOW keeps capacity available for the JSON answer.
            "thinkingConfig": {"thinkingLevel": "LOW"},
        },
    }
    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": config["api_key"]},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    candidate = (body.get("candidates") or [{}])[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason == "MAX_TOKENS":
        raise IncompleteGeminiResponse(
            "Gemini response stopped at maxOutputTokens; classification was not applied."
        )
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(str(part.get("text", "")) for part in parts).strip()
    if not text:
        raise ValueError("Gemini response contained no text candidate")
    usage = body.get("usageMetadata") or {}
    return text, {
        "prompt_tokens": usage.get("promptTokenCount"),
        "completion_tokens": usage.get("candidatesTokenCount"),
        "thoughts_tokens": usage.get("thoughtsTokenCount"),
        "total_tokens": usage.get("totalTokenCount"),
        "finish_reason": finish_reason,
    }
