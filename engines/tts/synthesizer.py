"""Hybrid TTS Synthesizer routing between zero-VRAM Edge-TTS and optional local F5-TTS."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Optional

import aiohttp
import edge_tts
from edge_tts.exceptions import NoAudioReceived

from engines.tts.cache import AudioLRUCache
from engines.tts.catalog import (
    DEFAULT_EDGE_VOICE,
    DEFAULT_JP_EDGE_VOICE,
    EDGE_FALLBACK_VOICE_MAP,
    EDGE_MALE_VOICES,
    EDGE_PROSODY_LIMITED_VOICES,
    EDGE_PROSODY_TTL_SECONDS,
    clamp_pitch_hz,
    clamp_rate_pct,
    format_edge_pitch,
    format_edge_rate,
    normalize_edge_short_name,
)
from engines.tts.f5_client import F5TtsClient
from engines.tts.text_processor import is_latin_dominant

logger = logging.getLogger("DiscordGeminiBot.tts")


class HybridTtsSynthesizer:
    """Routes between zero-VRAM Edge-TTS and optional local F5-TTS with instant fallback."""

    _MAX_F5_VOICE_CACHE_KEYS = F5TtsClient._MAX_F5_VOICE_CACHE_KEYS

    def __init__(
        self,
        tts_api_url: str = "",
        default_voice: str = DEFAULT_EDGE_VOICE,
        jp_voice: str = DEFAULT_JP_EDGE_VOICE,
        cache: Optional[AudioLRUCache] = None,
        offline_cooldown_seconds: float = 5.0,
        api_token: str = "",
    ):
        self.default_voice = (default_voice or DEFAULT_EDGE_VOICE).strip()
        self.jp_voice = (jp_voice or DEFAULT_JP_EDGE_VOICE).strip()
        self.cache = cache if cache is not None else AudioLRUCache()
        self.f5_client = F5TtsClient(
            tts_api_url=tts_api_url,
            offline_cooldown_seconds=offline_cooldown_seconds,
            api_token=api_token,
            on_invalidate_cache=self._purge_audio_cache_for_voice,
        )
        self._edge_no_pitch_voices: dict[str, float] = {
            v: float("inf") for v in EDGE_PROSODY_LIMITED_VOICES
        }
        self._edge_no_rate_voices: dict[str, float] = {
            v: float("inf") for v in EDGE_PROSODY_LIMITED_VOICES
        }
        self._edge_unreliable_until: dict[str, tuple[str, float]] = {}

    @property
    def tts_api_url(self) -> str:
        return self.f5_client.tts_api_url

    @tts_api_url.setter
    def tts_api_url(self, value: str) -> None:
        self.f5_client.tts_api_url = (value or "").strip().rstrip("/")

    @property
    def api_token(self) -> str:
        return self.f5_client.api_token

    @api_token.setter
    def api_token(self, value: str) -> None:
        self.f5_client.api_token = (value or "").strip()

    @property
    def offline_cooldown_seconds(self) -> float:
        return self.f5_client.offline_cooldown_seconds

    @offline_cooldown_seconds.setter
    def offline_cooldown_seconds(self, value: float) -> None:
        self.f5_client.offline_cooldown_seconds = max(1.0, float(value))

    @property
    def _f5_offline_until(self) -> float:
        return self.f5_client._f5_offline_until

    @_f5_offline_until.setter
    def _f5_offline_until(self, value: float) -> None:
        self.f5_client._f5_offline_until = float(value)

    @property
    def _cached_f5_voices(self) -> OrderedDict[str, list[dict[str, Any]]]:
        return self.f5_client._cached_f5_voices

    @_cached_f5_voices.setter
    def _cached_f5_voices(self, value: OrderedDict[str, list[dict[str, Any]]]) -> None:
        self.f5_client._cached_f5_voices = value

    @property
    def _http_session(self) -> Optional[aiohttp.ClientSession]:
        return self.f5_client._http_session

    @_http_session.setter
    def _http_session(self, value: Optional[aiohttp.ClientSession]) -> None:
        self.f5_client._http_session = value

    @property
    def _http_session_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self.f5_client._http_session_loop

    @_http_session_loop.setter
    def _http_session_loop(self, value: Optional[asyncio.AbstractEventLoop]) -> None:
        self.f5_client._http_session_loop = value

    def _admin_headers(self) -> dict[str, str]:
        return self.f5_client._admin_headers()

    async def _get_http_session(self) -> aiohttp.ClientSession:
        return await self.f5_client._get_http_session()

    async def close(self) -> None:
        await self.f5_client.close()

    def _purge_audio_cache_for_voice(self, voice_id: Optional[str] = None) -> None:
        """Purge cached audio clips after clone mutations."""
        if voice_id is None:
            self.cache.clear()
            return
        clean = (voice_id or "").strip()
        if clean:
            f5_key = clean if clean.startswith("f5:") else f"f5:{clean}"
            self.cache.delete_by_prefix(f"{f5_key}|")
            if clean != f5_key:
                self.cache.delete_by_prefix(f"{clean}|")

    def invalidate_voice_cache(self, voice_id: Optional[str] = None) -> None:
        """Purge cached audio clips and cached F5 voice listings after clone mutations."""
        self.f5_client.invalidate_voice_cache(voice_id)

    @staticmethod
    def _has_active_prosody_block(table: dict[str, float], voice_short: str) -> bool:
        if voice_short in EDGE_PROSODY_LIMITED_VOICES:
            return True
        expiry = table.get(voice_short)
        if expiry is None:
            return False
        if time.monotonic() < expiry:
            return True
        table.pop(voice_short, None)
        return False

    @property
    def is_f5_configured(self) -> bool:
        return self.f5_client.is_f5_configured

    def is_f5_in_cooldown(self) -> bool:
        return self.f5_client.is_f5_in_cooldown()

    def mark_f5_offline(self) -> None:
        self.f5_client.mark_f5_offline()

    def clear_f5_cooldown(self) -> None:
        self.f5_client.clear_f5_cooldown()

    async def check_f5_health(self, force: bool = False) -> Optional[dict[str, Any]]:
        return await self.f5_client.check_f5_health(force=force)

    def _get_cached_f5_voices(self, cache_uid_key: str) -> list[dict[str, Any]]:
        return self.f5_client._get_cached_f5_voices(cache_uid_key)

    async def fetch_f5_voices(
        self, user_id: Optional[int] = None, force: bool = False
    ) -> list[dict[str, Any]]:
        return await self.f5_client.fetch_f5_voices(user_id=user_id, force=force)

    async def unload_f5_model(self) -> Optional[dict[str, Any]]:
        return await self.f5_client.unload_f5_model()

    async def preload_f5_model(self) -> Optional[dict[str, Any]]:
        return await self.f5_client.preload_f5_model()

    async def update_f5_config(
        self,
        *,
        idle_unload_seconds: Optional[int] = None,
        preload_on_startup: Optional[bool] = None,
        offline_first: Optional[bool] = None,
    ) -> Optional[dict[str, Any]]:
        return await self.f5_client.update_f5_config(
            idle_unload_seconds=idle_unload_seconds,
            preload_on_startup=preload_on_startup,
            offline_first=offline_first,
        )

    async def reload_f5_voices(self) -> Optional[dict[str, Any]]:
        return await self.f5_client.reload_f5_voices()

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
        return await self.f5_client.register_f5_voice(
            audio_bytes=audio_bytes,
            owner_user_id=owner_user_id,
            slot=slot,
            name=name,
            prompt_text=prompt_text,
            public=public,
            description=description,
            max_per_user=max_per_user,
            max_total=max_total,
        )

    async def delete_f5_voice(
        self,
        voice_id: str,
        requester_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self.f5_client.delete_f5_voice(
            voice_id,
            requester_user_id=requester_user_id,
        )

    async def set_f5_voice_visibility(self, voice_id: str, *, public: bool) -> dict[str, Any]:
        return await self.f5_client.set_f5_voice_visibility(voice_id, public=public)

    async def synthesize_f5(
        self,
        text: str,
        f5_raw_id: str,
        user_id: Optional[int] = None,
        rate_pct: int = 0,
        pitch_hz: int = 0,
    ) -> tuple[bytes, str]:
        return await self.f5_client.synthesize_f5(
            text,
            f5_raw_id,
            user_id=user_id,
            rate_pct=rate_pct,
            pitch_hz=pitch_hz,
        )

    async def _stream_edge_once(
        self,
        text: str,
        edge_short_name: str,
        rate_pct: int,
        pitch_hz: int,
    ) -> bytes:
        async def _collect() -> bytes:
            communicate = edge_tts.Communicate(
                text=text,
                voice=edge_short_name,
                rate=format_edge_rate(rate_pct),
                pitch=format_edge_pitch(pitch_hz),
            )
            chunks: list[bytes] = []
            async for item in communicate.stream():
                if item.get("type") == "audio":
                    data = item.get("data")
                    if data:
                        chunks.append(data)
            audio_bytes = b"".join(chunks)
            if not audio_bytes:
                raise NoAudioReceived(f"Edge-TTS 未回傳音訊資料 (voice={edge_short_name})")
            return audio_bytes

        return await asyncio.wait_for(_collect(), timeout=15.0)

    async def synthesize_edge(
        self,
        text: str,
        edge_short_name: str,
        rate_pct: int = 0,
        pitch_hz: int = 0,
    ) -> tuple[bytes, str]:
        eff_pitch = (
            0
            if self._has_active_prosody_block(self._edge_no_pitch_voices, edge_short_name)
            else clamp_pitch_hz(pitch_hz)
        )
        eff_rate = (
            0
            if self._has_active_prosody_block(self._edge_no_rate_voices, edge_short_name)
            else clamp_rate_pct(rate_pct)
        )

        attempts: list[tuple[int, int]] = [(eff_rate, eff_pitch)]
        if eff_pitch != 0:
            attempts.append((eff_rate, 0))
        if eff_rate != 0:
            attempts.append((0, 0))

        last_exc: Optional[Exception] = None
        for idx, (r, p) in enumerate(attempts):
            try:
                audio_bytes = await asyncio.wait_for(
                    self._stream_edge_once(text, edge_short_name, r, p),
                    timeout=15.0,
                )
                if idx > 0:
                    expiry = time.monotonic() + EDGE_PROSODY_TTL_SECONDS
                    if eff_pitch != 0 and p == 0:
                        self._edge_no_pitch_voices[edge_short_name] = expiry
                    if eff_rate != 0 and r == 0:
                        self._edge_no_rate_voices[edge_short_name] = expiry
                return audio_bytes, "mp3"
            except (NoAudioReceived, RuntimeError) as exc:
                last_exc = exc
                if idx + 1 < len(attempts):
                    next_r, next_p = attempts[idx + 1]
                    logger.warning(
                        f"⚠️ Edge-TTS ({edge_short_name}, rate={r:+d}%, pitch={p:+d}Hz) 無音訊，"
                        f"自動降級為 rate={next_r:+d}%, pitch={next_p:+d}Hz 重試: {exc}"
                    )
                    continue

        if edge_short_name not in EDGE_PROSODY_LIMITED_VOICES:
            try:
                await asyncio.sleep(0.25)
                audio_bytes = await asyncio.wait_for(
                    self._stream_edge_once(text, edge_short_name, 0, 0),
                    timeout=15.0,
                )
                expiry = time.monotonic() + EDGE_PROSODY_TTL_SECONDS
                if eff_pitch != 0:
                    self._edge_no_pitch_voices[edge_short_name] = expiry
                if eff_rate != 0:
                    self._edge_no_rate_voices[edge_short_name] = expiry
                return audio_bytes, "mp3"
            except Exception as retry_exc:
                raise retry_exc from last_exc
        if last_exc is not None:
            raise last_exc
        raise NoAudioReceived(f"Edge-TTS 未回傳音訊資料 (voice={edge_short_name})")

    async def _synthesize_edge_cached(
        self,
        text: str,
        edge_short_name: str,
        *,
        rate_pct: int = 0,
        pitch_hz: int = 0,
        cacheable: bool = True,
        cache_key: Optional[str] = None,
        check_cache: bool = True,
    ) -> tuple[bytes, str, bool]:
        """Synthesize via Edge-TTS with LRU cache lookup/store; returns (bytes, ext, from_cache)."""
        clamped_rate = clamp_rate_pct(rate_pct)
        clamped_pitch = clamp_pitch_hz(pitch_hz)
        key = cache_key or f"edge:{edge_short_name}||{clamped_rate}|{clamped_pitch}|{text}"
        if cacheable and check_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return cached[0], cached[1], True
        audio_bytes, ext = await self.synthesize_edge(
            text, edge_short_name, rate_pct=rate_pct, pitch_hz=pitch_hz
        )
        if cacheable:
            self.cache.put(key, audio_bytes, ext)
        return audio_bytes, ext, False

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        *,
        user_id: Optional[int] = None,
        rate_pct: int = 0,
        pitch_hz: int = 0,
        fallback_voice: Optional[str] = None,
    ) -> tuple[bytes, str, bool]:
        """Synthesize speech and return (audio_bytes, ext, used_fallback)."""
        clean = (text or "").strip()
        if not clean:
            raise ValueError("合成文字不可為空")

        vid = (voice_id or "").strip() or self.default_voice
        cacheable = len(clean) <= 60
        uid_tag = str(user_id) if (vid.startswith("f5:") and user_id is not None) else ""
        cache_key = f"{vid}|{uid_tag}|{clamp_rate_pct(rate_pct)}|{clamp_pitch_hz(pitch_hz)}|{clean}"

        if cacheable:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached[0], cached[1], False

        if vid.startswith("f5:"):
            raw_id = vid[3:]
            try:
                audio_bytes, ext = await self.synthesize_f5(
                    clean, raw_id, user_id=user_id, rate_pct=rate_pct, pitch_hz=pitch_hz
                )
                if cacheable:
                    self.cache.put(cache_key, audio_bytes, ext)
                return audio_bytes, ext, False
            except Exception as exc:
                logger.warning(f"⚠️ F5-TTS ({raw_id}) 失敗，自動降級為 Edge-TTS: {exc}")
                fb_short = normalize_edge_short_name(fallback_voice or self.default_voice)
                audio_bytes, ext, _ = await self._synthesize_edge_cached(
                    clean, fb_short, rate_pct=rate_pct, pitch_hz=pitch_hz, cacheable=cacheable
                )
                return audio_bytes, ext, True

        short_name = normalize_edge_short_name(vid, fallback=self.default_voice)
        fb_short = normalize_edge_short_name(fallback_voice or self.default_voice)

        # Direct 0ms Japanese Kana routing: Microsoft's zh-* voices either fail with NoAudioReceived on pure Kana
        # or silently swallow Kana and read only Kanji in Mandarin. Route Kana chunks to gender-matched ja-JP voice.
        if not short_name.startswith("ja-") and re.search(r"[\u3040-\u30FF]", clean):
            jp_paired = (
                "ja-JP-KeitaNeural"
                if short_name in EDGE_MALE_VOICES
                else normalize_edge_short_name(self.jp_voice, fallback=DEFAULT_JP_EDGE_VOICE)
            )
            try:
                audio_bytes, ext, _ = await self._synthesize_edge_cached(
                    clean, jp_paired, rate_pct=rate_pct, pitch_hz=pitch_hz, cacheable=cacheable
                )
                return audio_bytes, ext, True
            except Exception:
                pass

        # Fast-path circuit breaker & Latin routing for prosody-limited Edge endpoints (e.g. zh-TW-YunJheNeural)
        now_mono = time.monotonic()
        cb_entry = self._edge_unreliable_until.get(short_name)
        if cb_entry is not None:
            if now_mono < cb_entry[1]:
                paired_short = cb_entry[0]
            else:
                self._edge_unreliable_until.pop(short_name, None)
                paired_short = ""
        elif (
            short_name in EDGE_PROSODY_LIMITED_VOICES
            and short_name in EDGE_FALLBACK_VOICE_MAP
            and is_latin_dominant(clean)
        ):
            paired_short = EDGE_FALLBACK_VOICE_MAP[short_name]
            self._edge_unreliable_until[short_name] = (paired_short, now_mono + 600.0)
        else:
            paired_short = ""

        if paired_short:
            try:
                audio_bytes, ext, _ = await self._synthesize_edge_cached(
                    clean, paired_short, rate_pct=rate_pct, pitch_hz=pitch_hz, cacheable=cacheable
                )
                return audio_bytes, ext, True
            except Exception:
                self._edge_unreliable_until.pop(short_name, None)

        try:
            audio_bytes, ext, _ = await self._synthesize_edge_cached(
                clean,
                short_name,
                rate_pct=rate_pct,
                pitch_hz=pitch_hz,
                cacheable=cacheable,
                cache_key=cache_key,
                check_cache=False,
            )
            return audio_bytes, ext, False
        except Exception as exc:
            candidates: list[tuple[str, int, int]] = []
            seen_candidates: set[tuple[str, int, int]] = set()

            def _add_candidate(cand_short: str, cand_rate: int, cand_pitch: int) -> None:
                if not cand_short or cand_short == short_name:
                    return
                key = (cand_short, clamp_rate_pct(cand_rate), clamp_pitch_hz(cand_pitch))
                if key not in seen_candidates:
                    seen_candidates.add(key)
                    candidates.append(key)

            if short_name in EDGE_FALLBACK_VOICE_MAP:
                _add_candidate(EDGE_FALLBACK_VOICE_MAP[short_name], rate_pct, pitch_hz)
            if re.search(r"[\u3040-\u30FF]", clean) and not short_name.startswith("ja-"):
                jp_short = normalize_edge_short_name(self.jp_voice, fallback=DEFAULT_JP_EDGE_VOICE)
                _add_candidate(jp_short, rate_pct, pitch_hz)
            _add_candidate(fb_short, 0, 0)

            if not candidates:
                raise

            for cand_short, cand_rate, cand_pitch in candidates:
                try:
                    audio_bytes, ext, from_cache = await self._synthesize_edge_cached(
                        clean,
                        cand_short,
                        rate_pct=cand_rate,
                        pitch_hz=cand_pitch,
                        cacheable=cacheable,
                    )
                    if not cand_short.startswith("ja-"):
                        self._edge_unreliable_until[short_name] = (cand_short, time.monotonic() + 600.0)
                    mode_label = "快取朗讀" if from_cache else "朗讀"
                    logger.warning(
                        f"⚠️ Edge-TTS ({short_name}) 合成失敗，自動改用備援聲線 ({cand_short}) {mode_label}: {exc}"
                    )
                    return audio_bytes, ext, True
                except Exception as fb_exc:
                    logger.warning(f"⚠️ 備援聲線 ({cand_short}) 亦合成失敗: {fb_exc}")
            raise
