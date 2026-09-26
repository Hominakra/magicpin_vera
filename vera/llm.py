from __future__ import annotations

import os
import json
import re
from typing import Any, Optional
from urllib import request as urlrequest, error as urlerror


def llm_enabled() -> bool:
    return bool(os.getenv("VERA_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))


def complete(prompt: str, system: str, timeout: int = 20) -> Optional[str]:
    """Best-effort LLM call. Returns None on any failure so templates can take over."""
    provider = (os.getenv("VERA_LLM_PROVIDER") or "openai").lower()
    api_key = os.getenv("VERA_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""
    model = os.getenv("VERA_LLM_MODEL") or ""
    if not api_key and provider != "ollama":
        return None
    try:
        if provider == "anthropic":
            return _anthropic(api_key, model or "claude-3-5-sonnet-20241022", prompt, system, timeout)
        if provider == "gemini":
            return _gemini(api_key, model or "gemini-1.5-flash", prompt, system, timeout)
        if provider == "ollama":
            return _ollama(os.getenv("OLLAMA_URL") or "http://localhost:11434", model or "llama3", prompt, system, timeout)
        # openai-compatible (openai, groq, deepseek, openrouter)
        return _openai_compat(provider, api_key, model, prompt, system, timeout)
    except Exception:
        return None


def _openai_compat(provider: str, api_key: str, model: str, prompt: str, system: str, timeout: int) -> str:
    urls = {
        "openai": ("https://api.openai.com/v1/chat/completions", model or "gpt-4o-mini"),
        "groq": ("https://api.groq.com/openai/v1/chat/completions", model or "llama-3.1-8b-instant"),
        "deepseek": ("https://api.deepseek.com/v1/chat/completions", model or "deepseek-chat"),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", model or "openai/gpt-4o-mini"),
    }
    url, default_model = urls.get(provider, urls["openai"])
    body = json.dumps({
        "model": default_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 500,
    }).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://magicpin.com"
    req = urlrequest.Request(url, data=body, headers=headers)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _anthropic(api_key: str, model: str, prompt: str, system: str, timeout: int) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": 500,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urlrequest.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"].strip()


def _gemini(api_key: str, model: str, prompt: str, system: str, timeout: int) -> str:
    import time
    from urllib.error import HTTPError
    body = json.dumps({
        "contents": [{"parts": [{"text": f"{system}\n\n{prompt}"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 800},
    }).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(10):
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 9:
                time.sleep(30)
                continue
            raise e
    parts = (data["candidates"][0].get("content") or {}).get("parts") or []
    if not parts:
        raise ValueError("Empty content from Gemini (thinking budget exhausted?)")
    return parts[0]["text"].strip()


def _ollama(api_url: str, model: str, prompt: str, system: str, timeout: int) -> str:
    req = urlrequest.Request(
        f"{api_url.rstrip('/')}/api/generate",
        data=json.dumps({
            "model": model,
            "prompt": f"{system}\n\n{prompt}",
            "stream": False,
            "options": {"temperature": 0},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "").strip()


def extract_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None
