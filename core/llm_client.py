"""Unified Gemini async client wrapper with safety settings and model fallback."""

import asyncio
import logging
from typing import Optional

from google import genai
from google.genai import types

DEFAULT_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]


def get_safety_settings(disable_safety_settings: bool = True) -> Optional[list[types.SafetySetting]]:
    """Return BLOCK_NONE safety settings when disabled, or None for API defaults."""
    return list(DEFAULT_SAFETY_SETTINGS) if disable_safety_settings else None


async def generate_with_fallback(
    client: genai.Client,
    models_to_try: list[str],
    contents,
    config: types.GenerateContentConfig,
    timeout: float = 30.0,
    context_label: str = "AI",
    logger: Optional[logging.Logger] = None,
) -> tuple[types.GenerateContentResponse, str]:
    """Call Gemini asynchronously with timeout protection and ordered fallback models."""
    last_error: Optional[Exception] = None
    for model_name in models_to_try:
        try:
            if logger:
                logger.info(f"🤖 ({context_label}) 正在向 {model_name} 請求回答...")
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=timeout,
            )
            if resp and resp.text:
                return resp, model_name
        except Exception as err:
            last_error = err
            if logger:
                logger.warning(f"⚠️ ({context_label}) 模型 {model_name} 無法回應 ({err})，自動嘗試下一個備用模型...")
            continue

    if last_error:
        raise last_error
    raise RuntimeError(f"({context_label}) 所有模型皆無法回應。")
