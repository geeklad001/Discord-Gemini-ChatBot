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


def sanitize_contents_for_gemini(
    contents,
    context_label: str = "AI",
    logger: Optional[logging.Logger] = None,
):
    """Ensure list-form Gemini contents never end with a model turn (prevents 400 INVALID_ARGUMENT)."""
    if not isinstance(contents, list):
        return contents

    sanitized = list(contents)
    while sanitized:
        last = sanitized[-1]
        last_role = (
            last.role
            if isinstance(last, types.Content)
            else (last.get("role") if isinstance(last, dict) else None)
        )
        if last_role == "model":
            sanitized.pop()
            if logger:
                logger.warning(
                    f"⚠️ ({context_label}) 偵測到送往 Gemini 的對話結尾為 model 回合，已自動裁剪以防止 400 錯誤。"
                )
        else:
            break
    return sanitized


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
    prepared_contents = sanitize_contents_for_gemini(
        contents,
        context_label=context_label,
        logger=logger,
    )
    if isinstance(prepared_contents, list) and not prepared_contents:
        raise ValueError(f"({context_label}) 對話內容不包含有效的 user 回合，無法向 Gemini 請求。")

    last_error: Optional[Exception] = None
    for model_name in models_to_try:
        try:
            if logger:
                logger.info(f"🤖 ({context_label}) 正在向 {model_name} 請求回答...")
            call_contents = list(prepared_contents) if isinstance(prepared_contents, list) else prepared_contents
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=call_contents,
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
