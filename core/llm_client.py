"""Unified Gemini async client wrapper with safety settings, cooldowns, and model fallback."""

import asyncio
import logging
import re
import time
from typing import Optional

from google import genai
from google.genai import types

_INVISIBLE_CHARS_RE = re.compile(r"[\s\u200b\u200c\u200d\u2060\ufeff]+")
_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}


def reset_model_cooldowns() -> None:
    """Clear all recorded model rate-limit cooldowns (useful for reload or tests)."""
    _MODEL_COOLDOWN_UNTIL.clear()


def get_active_model_cooldowns() -> dict[str, float]:
    """Return remaining cooldown seconds keyed by model name (pruning expired entries)."""
    now = time.monotonic()
    expired = [m for m, until in _MODEL_COOLDOWN_UNTIL.items() if until <= now]
    for m in expired:
        _MODEL_COOLDOWN_UNTIL.pop(m, None)
    return {m: round(until - now, 1) for m, until in _MODEL_COOLDOWN_UNTIL.items()}


def _is_rate_limit_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 429:
        return True
    msg = str(err).upper()
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "RATE_LIMIT" in msg or "QUOTA" in msg


def _is_not_found_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code == 404:
        return True
    msg = str(err).upper()
    return "404" in msg or "NOT_FOUND" in msg


def _is_permanent_model_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code in (400, 401, 403, 404):
        return True
    msg = str(err).upper()
    return any(
        tok in msg
        for tok in (
            "404",
            "NOT_FOUND",
            "400 INVALID_ARGUMENT",
            "INVALID_ARGUMENT",
            "PERMISSION_DENIED",
            "API_KEY_INVALID",
            "UNAUTHENTICATED",
        )
    )


def _adapt_config_for_model(
    model_name: str,
    config: types.GenerateContentConfig,
) -> types.GenerateContentConfig:
    """Clone and adapt GenerateContentConfig for model-family compatibility."""
    if not isinstance(config, types.GenerateContentConfig):
        return config
    adapted = config.model_copy(deep=True)
    name_l = (model_name or "").strip().lower()

    # Legacy Gemini 2.0 / 2.5 / Gemma models reject Gemini 3+ `thinking_level` with 400 INVALID_ARGUMENT
    if name_l.startswith(("gemini-2.5", "gemini-2.0", "gemma")):
        tc = getattr(adapted, "thinking_config", None)
        if tc is not None and getattr(tc, "thinking_level", None) is not None:
            adapted.thinking_config = None

    # Avoid strict temperature=0.0 on Gemini 3+ / -latest models
    if name_l.startswith(("gemini-3", "gemini-4")) or name_l.endswith("-latest"):
        if adapted.temperature is not None and adapted.temperature == 0.0:
            adapted.temperature = 0.1

    return adapted


def has_visible_text(text: Optional[str]) -> bool:
    """判斷模型輸出是否包含真實可見字元（排除純空白、純換行與零寬隱形字元）。"""
    if not text:
        return False
    return bool(_INVISIBLE_CHARS_RE.sub("", text))

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
    final_fallback_model: Optional[str] = None,
    final_fallback_delay: float = 1.5,
    use_cooldown: bool = True,
    cooldown_seconds: float = 45.0,
    not_found_cooldown_seconds: float = 600.0,
) -> tuple[types.GenerateContentResponse, str]:
    """Call Gemini asynchronously with timeout protection, 429 cooldown, and final Flash-Lite fallback."""
    prepared_contents = sanitize_contents_for_gemini(
        contents,
        context_label=context_label,
        logger=logger,
    )
    if isinstance(prepared_contents, list) and not prepared_contents:
        raise ValueError(f"({context_label}) 對話內容不包含有效的 user 回合，無法向 Gemini 請求。")

    deduped_models = list(dict.fromkeys(m.strip() for m in models_to_try if m and m.strip()))
    now = time.monotonic()
    if use_cooldown:
        active_models = [m for m in deduped_models if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now]
        cooled_models = [m for m in deduped_models if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) > now]
        ordered_models = active_models + cooled_models if active_models else deduped_models
    else:
        ordered_models = deduped_models

    last_error: Optional[Exception] = None
    permanent_failed: set[str] = set()

    async def _try_single_model(model_name: str, *, is_final_pass: bool = False) -> Optional[types.GenerateContentResponse]:
        nonlocal last_error
        try:
            if logger:
                suffix = "（最終 Flash-Lite 保底）" if is_final_pass else ""
                logger.info(f"🤖 ({context_label}) 正在向 {model_name} 請求回答{suffix}...")
            call_contents = list(prepared_contents) if isinstance(prepared_contents, list) else prepared_contents
            call_config = _adapt_config_for_model(model_name, config)
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=call_contents,
                    config=call_config,
                ),
                timeout=timeout,
            )
            if resp and has_visible_text(resp.text):
                if use_cooldown:
                    _MODEL_COOLDOWN_UNTIL.pop(model_name, None)
                return resp

            finish_reason = None
            if resp and getattr(resp, "candidates", None):
                finish_reason = getattr(resp.candidates[0], "finish_reason", None)
            usage = getattr(resp, "usage_metadata", None) if resp else None
            thoughts_tok = getattr(usage, "thoughts_token_count", 0) if usage else 0
            out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
            raw_len = len(resp.text) if (resp and resp.text) else 0
            empty_err = RuntimeError(
                f"模型 {model_name} 回傳空白或純不可見字元內容 "
                f"(finish_reason={finish_reason}, thoughts_tokens={thoughts_tok}, "
                f"output_tokens={out_tok}, raw_len={raw_len})"
            )
            last_error = empty_err
            if logger and not is_final_pass:
                logger.warning(f"⚠️ ({context_label}) {empty_err}，自動嘗試下一個備用模型...")
            return None
        except Exception as err:
            last_error = err
            if _is_permanent_model_error(err):
                permanent_failed.add(model_name)
                if use_cooldown and _is_not_found_error(err):
                    _MODEL_COOLDOWN_UNTIL[model_name] = time.monotonic() + not_found_cooldown_seconds
            elif use_cooldown and _is_rate_limit_error(err):
                _MODEL_COOLDOWN_UNTIL[model_name] = time.monotonic() + cooldown_seconds
            if logger and not is_final_pass:
                logger.warning(f"⚠️ ({context_label}) 模型 {model_name} 無法回應 ({err})，自動嘗試下一個備用模型...")
            return None

    for model_name in ordered_models:
        resp = await _try_single_model(model_name)
        if resp is not None:
            return resp, model_name

    final_name = (final_fallback_model or "").strip()
    if final_name and final_name not in permanent_failed:
        if final_fallback_delay > 0 and ordered_models:
            await asyncio.sleep(final_fallback_delay)
        resp = await _try_single_model(final_name, is_final_pass=True)
        if resp is not None:
            return resp, final_name

    if last_error:
        raise last_error
    raise RuntimeError(f"({context_label}) 所有模型皆無法回應。")
