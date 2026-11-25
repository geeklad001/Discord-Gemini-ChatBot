"""HTTP client for the optional local F5-TTS voice cloning & synthesis service."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from collections import OrderedDict
from typing import Any, Callable, Optional

import aiohttp

from engines.tts.catalog import (
    MANAGED_VOICE_ID_RE,
    apply_wav_pitch_shift,
    clamp_pitch_hz,
    rate_pct_to_f5_speed,
)

logger = logging.getLogger("DiscordGeminiBot.tts")


class F5TtsClient:
    """Async HTTP client managing lazy loop-aware ClientSession, cooldown state, and F5 endpoints."""

    _MAX_F5_VOICE_CACHE_KEYS = 256

    def __init__(
        self,
        tts_api_url: str = "",
        offline_cooldown_seconds: float = 5.0,
        api_token: str = "",
        on_invalidate_cache: Optional[Callable[[Optional[str]], None]] = None,
    ):
        self.tts_api_url = (tts_api_url or "").strip().rstrip("/")
        self.api_token = (api_token or "").strip()
        self.offline_cooldown_seconds = max(1.0, float(offline_cooldown_seconds))
        self._on_invalidate_cache = on_invalidate_cache
        self._f5_offline_until: float = 0.0
        self._cached_f5_voices: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._http_session_loop: Optional[asyncio.AbstractEventLoop] = None

    def _admin_headers(self) -> dict[str, str]:
        return {"X-TTS-Admin-Token": self.api_token} if self.api_token else {}

    async def _get_http_session(self) -> aiohttp.ClientSession:
        loop = asyncio.get_running_loop()
        if (
            self._http_session is None
            or self._http_session.closed
            or self._http_session_loop is not loop
        ):
            if self._http_session is not None and not self._http_session.closed:
                try:
                    await self._http_session.close()
                except Exception:
                    pass
            self._http_session = aiohttp.ClientSession()
            self._http_session_loop = loop
        return self._http_session

    async def close(self) -> None:
        session = self._http_session
        self._http_session = None
        self._http_session_loop = None
        if session is not None and not session.closed:
            await session.close()

    def invalidate_voice_cache(self, voice_id: Optional[str] = None) -> None:
        self._cached_f5_voices.clear()
        if self._on_invalidate_cache is not None:
            self._on_invalidate_cache(voice_id)

    @property
    def is_f5_configured(self) -> bool:
        return bool(self.tts_api_url)

    def is_f5_in_cooldown(self) -> bool:
        return time.monotonic() < self._f5_offline_until

    def mark_f5_offline(self) -> None:
        self._f5_offline_until = time.monotonic() + self.offline_cooldown_seconds

    def clear_f5_cooldown(self) -> None:
        self._f5_offline_until = 0.0

    def _get_cached_f5_voices(self, cache_uid_key: str) -> list[dict[str, Any]]:
        cached = self._cached_f5_voices.get(cache_uid_key)
        if cached is None:
            return []
        self._cached_f5_voices.move_to_end(cache_uid_key)
        return list(cached)

    @staticmethod
    def _extract_error_detail(raw_text: str) -> str:
        try:
            payload = json.loads(raw_text)
            return str(payload.get("detail") or raw_text)
        except ValueError:
            return raw_text

    async def _f5_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        data: Any = None,
        timeout_total: float = 5.0,
        timeout_connect: float = 2.0,
        expect_bytes: bool = False,
        mark_offline_on_error: bool = True,
    ) -> tuple[int, Any]:
        """Execute an F5-TTS HTTP request with shared session, headers, and offline cooldown handling."""
        session = await self._get_http_session()
        timeout = aiohttp.ClientTimeout(total=timeout_total, connect=timeout_connect)
        url = f"{self.tts_api_url}{path}"
        try:
            async with session.request(
                method,
                url,
                params=params,
                data=data,
                headers=self._admin_headers(),
                timeout=timeout,
                allow_redirects=False,
            ) as resp:
                if resp.status == 200:
                    body = await resp.read() if expect_bytes else await resp.json()
                    self.clear_f5_cooldown()
                    return resp.status, body
                return resp.status, await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            if mark_offline_on_error:
                self.mark_f5_offline()
            raise ConnectionError(f"無法連線至本機 F5-TTS 服務 ({self.tts_api_url}): {exc}") from exc

    async def check_f5_health(self, force: bool = False) -> Optional[dict[str, Any]]:
        if not self.is_f5_configured:
            return None
        if not force and self.is_f5_in_cooldown():
            return None
        try:
            status, body = await self._f5_request(
                "GET",
                "/",
                timeout_total=4.0,
                timeout_connect=2.0,
                mark_offline_on_error=False,
            )
            return body if status == 200 else None
        except ConnectionError:
            return None

    async def fetch_f5_voices(
        self, user_id: Optional[int] = None, force: bool = False
    ) -> list[dict[str, Any]]:
        if not self.is_f5_configured:
            return []
        cache_uid_key = str(user_id) if user_id is not None else "__public__"
        if not force and self.is_f5_in_cooldown():
            return self._get_cached_f5_voices(cache_uid_key)
        params = {"user_id": str(user_id)} if user_id is not None else None
        try:
            status, payload = await self._f5_request(
                "GET",
                "/voices",
                params=params,
                timeout_total=4.0,
                timeout_connect=2.0,
                mark_offline_on_error=False,
            )
            if status != 200:
                return self._get_cached_f5_voices(cache_uid_key)
        except ConnectionError:
            return self._get_cached_f5_voices(cache_uid_key)

        raw_list = payload.get("voices") if isinstance(payload, dict) else None
        if not isinstance(raw_list, list):
            return self._get_cached_f5_voices(cache_uid_key)
        normalized: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            raw_id = str(item.get("id") or "").strip()
            if not raw_id:
                continue
            normalized.append(
                {
                    "id": f"f5:{raw_id}",
                    "raw_id": raw_id,
                    "name": str(item.get("name") or raw_id),
                    "description": str(item.get("description") or ""),
                    "public": bool(item.get("public", False)),
                    "speed": float(item.get("speed", 1.0)),
                }
            )
        self._cached_f5_voices[cache_uid_key] = normalized
        self._cached_f5_voices.move_to_end(cache_uid_key)
        while len(self._cached_f5_voices) > self._MAX_F5_VOICE_CACHE_KEYS:
            self._cached_f5_voices.popitem(last=False)
        return list(normalized)

    async def unload_f5_model(self) -> Optional[dict[str, Any]]:
        if not self.is_f5_configured:
            return None
        try:
            status, body = await self._f5_request(
                "POST",
                "/unload",
                timeout_total=5.0,
                timeout_connect=1.5,
                mark_offline_on_error=True,
            )
            if status == 200:
                return body
            logger.warning(f"⚠️ F5-TTS unload 回應異常 (HTTP {status})")
            return None
        except ConnectionError as exc:
            logger.warning(f"⚠️ 無法連線至本機 F5-TTS 服務 ({self.tts_api_url}/unload): {exc}")
            return None

    async def preload_f5_model(self) -> Optional[dict[str, Any]]:
        """Preload the F5-TTS model into GPU/CPU memory via POST /preload."""
        if not self.is_f5_configured:
            return None
        try:
            status, body = await self._f5_request(
                "POST",
                "/preload",
                timeout_total=90.0,
                timeout_connect=3.0,
                mark_offline_on_error=True,
            )
            if status == 200:
                return body
            logger.warning(f"⚠️ F5-TTS preload 回應異常 (HTTP {status}): {self._extract_error_detail(str(body))}")
            return None
        except ConnectionError as exc:
            logger.warning(f"⚠️ 無法連線至本機 F5-TTS 服務 ({self.tts_api_url}/preload): {exc}")
            return None

    async def update_f5_config(
        self,
        *,
        idle_unload_seconds: Optional[int] = None,
        preload_on_startup: Optional[bool] = None,
        offline_first: Optional[bool] = None,
    ) -> Optional[dict[str, Any]]:
        """Update and persist non-secret F5-TTS server residency/preload preferences via POST /config."""
        if not self.is_f5_configured:
            return None
        form = aiohttp.FormData()
        has_field = False
        if idle_unload_seconds is not None:
            form.add_field("idle_unload_seconds", str(max(0, min(86400, int(idle_unload_seconds)))))
            has_field = True
        if preload_on_startup is not None:
            form.add_field("preload_on_startup", "true" if preload_on_startup else "false")
            has_field = True
        if offline_first is not None:
            form.add_field("offline_first", "true" if offline_first else "false")
            has_field = True
        try:
            status, body = await self._f5_request(
                "POST",
                "/config",
                data=form if has_field else None,
                timeout_total=5.0,
                timeout_connect=2.0,
                mark_offline_on_error=True,
            )
            if status == 200:
                return body
            logger.warning(f"⚠️ F5-TTS config 回應異常 (HTTP {status}): {self._extract_error_detail(str(body))}")
            return None
        except ConnectionError as exc:
            logger.warning(f"⚠️ 無法連線至本機 F5-TTS 服務 ({self.tts_api_url}/config): {exc}")
            return None

    async def reload_f5_voices(self) -> Optional[dict[str, Any]]:
        if not self.is_f5_configured:
            return None
        try:
            status, body = await self._f5_request(
                "POST",
                "/reload_voices",
                timeout_total=5.0,
                timeout_connect=1.5,
                mark_offline_on_error=True,
            )
            if status == 200:
                self.invalidate_voice_cache()
                return body
            logger.warning(f"⚠️ F5-TTS reload_voices 回應異常 (HTTP {status})")
            return None
        except ConnectionError as exc:
            logger.warning(f"⚠️ 無法連線至本機 F5-TTS 服務 ({self.tts_api_url}/reload_voices): {exc}")
            return None

    async def register_f5_voice(
        self,
        *,
        audio_bytes: bytes,
        owner_user_id: int,
        slot: int,
        name: str,
        prompt_text: str,
        public: bool = False,
        description: str = "",
        max_per_user: int = 2,
        max_total: int = 40,
    ) -> dict[str, Any]:
        if not self.is_f5_configured:
            raise ConnectionError("未設定本機 F5-TTS 服務網址，無法註冊聲線")
        form = aiohttp.FormData()
        form.add_field("audio", audio_bytes, filename=f"u_{owner_user_id}_s{slot}.wav", content_type="audio/wav")
        form.add_field("owner_user_id", str(int(owner_user_id)))
        form.add_field("slot", str(int(slot)))
        form.add_field("name", str(name))
        form.add_field("prompt_text", str(prompt_text))
        form.add_field("description", str(description))
        form.add_field("public", "true" if public else "false")
        form.add_field("max_per_user", str(int(max_per_user)))
        form.add_field("max_total", str(int(max_total)))

        status, body = await self._f5_request(
            "POST",
            "/register_voice",
            data=form,
            timeout_total=20.0,
            timeout_connect=4.0,
            mark_offline_on_error=True,
        )
        if status == 200:
            self.invalidate_voice_cache(
                str((body or {}).get("voice_id") or f"u_{int(owner_user_id)}_s{int(slot)}")
            )
            return body
        detail = self._extract_error_detail(body)
        raise RuntimeError(f"F5-TTS 註冊聲線失敗 (HTTP {status}): {detail}")

    async def delete_f5_voice(
        self,
        voice_id: str,
        requester_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        if not self.is_f5_configured:
            raise ConnectionError("未設定本機 F5-TTS 服務網址，無法刪除聲線")
        clean_vid = (voice_id or "").strip()
        if not MANAGED_VOICE_ID_RE.match(clean_vid):
            raise ValueError(f"非法的聲線識別碼格式: {clean_vid}")
        encoded_vid = urllib.parse.quote(clean_vid, safe="")
        params = {"requester_user_id": str(int(requester_user_id))} if requester_user_id is not None else None
        status, body = await self._f5_request(
            "DELETE",
            f"/voices/{encoded_vid}",
            params=params,
            timeout_total=12.0,
            timeout_connect=4.0,
            mark_offline_on_error=True,
        )
        if status == 200:
            self.invalidate_voice_cache(clean_vid)
            return body
        if status == 404:
            self.invalidate_voice_cache(clean_vid)
            return {"status": "not_found", "voice_id": clean_vid}
        detail = self._extract_error_detail(body)
        raise RuntimeError(f"F5-TTS 刪除聲線失敗 (HTTP {status}): {detail}")

    async def set_f5_voice_visibility(self, voice_id: str, *, public: bool) -> dict[str, Any]:
        if not self.is_f5_configured:
            raise ConnectionError("未設定本機 F5-TTS 服務網址，無法調整聲線權限")
        clean_vid = (voice_id or "").strip()
        if not MANAGED_VOICE_ID_RE.match(clean_vid):
            raise ValueError(f"非法的聲線識別碼格式: {clean_vid}")
        encoded_vid = urllib.parse.quote(clean_vid, safe="")
        form = aiohttp.FormData()
        form.add_field("public", "true" if public else "false")
        status, body = await self._f5_request(
            "POST",
            f"/voices/{encoded_vid}/visibility",
            data=form,
            timeout_total=12.0,
            timeout_connect=4.0,
            mark_offline_on_error=True,
        )
        if status == 200:
            self.invalidate_voice_cache(clean_vid)
            return body
        detail = self._extract_error_detail(body)
        raise RuntimeError(f"F5-TTS 調整聲線權限失敗 (HTTP {status}): {detail}")

    async def synthesize_f5(
        self,
        text: str,
        f5_raw_id: str,
        user_id: Optional[int] = None,
        rate_pct: int = 0,
        pitch_hz: int = 0,
    ) -> tuple[bytes, str]:
        if not self.is_f5_configured:
            raise ConnectionError("未設定 TTS_API_URL，無法呼叫 F5-TTS")
        if self.is_f5_in_cooldown():
            raise ConnectionError("F5-TTS 處於離線冷卻保護中")

        last_exc: Optional[Exception] = None
        for attempt in range(2):
            form = aiohttp.FormData()
            form.add_field("tts_text", text)
            form.add_field("voice_id", f5_raw_id)
            if user_id is not None:
                form.add_field("user_id", str(user_id))
            if rate_pct != 0:
                form.add_field("speed", str(rate_pct_to_f5_speed(rate_pct)))

            try:
                status, body = await self._f5_request(
                    "POST",
                    "/tts_by_voice",
                    data=form,
                    timeout_total=60.0,
                    timeout_connect=4.0,
                    expect_bytes=True,
                    mark_offline_on_error=False,
                )
            except ConnectionError as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.35)
                    continue
                self.mark_f5_offline()
                raise

            if status == 200:
                data = body
                if clamp_pitch_hz(pitch_hz) != 0:
                    data = await apply_wav_pitch_shift(data, pitch_hz)
                return data, "wav"
            detail = str(body)
            if status in (401, 403):
                raise PermissionError(f"F5-TTS 聲線權限拒絕 ({f5_raw_id}): {detail}")
            if status == 404:
                raise FileNotFoundError(f"F5-TTS 聲線不存在 ({f5_raw_id}): {detail}")
            last_exc = RuntimeError(f"F5-TTS HTTP {status}: {detail}")
            if status >= 500 and attempt == 0:
                await asyncio.sleep(0.35)
                continue
            if status >= 500:
                self.mark_f5_offline()
            raise last_exc

        self.mark_f5_offline()
        raise ConnectionError(f"F5-TTS 連線失敗: {last_exc}")
