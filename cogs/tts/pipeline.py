"""Async two-stage per-guild TTS synthesis and FFmpeg playback pipeline."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from engines import tts_engine


def sweep_orphaned_temp_files(max_age_seconds: float = 300.0) -> None:
    """Remove stale `dgbot_tts_*` temporary audio files older than max_age_seconds."""
    cutoff = time.time() - max(1.0, float(max_age_seconds))
    try:
        with os.scandir(tempfile.gettempdir()) as entries:
            for entry in entries:
                if not entry.name.startswith("dgbot_tts_"):
                    continue
                try:
                    if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(entry.path)
                except OSError:
                    pass
    except OSError:
        pass


def resolve_user_voice_channel(
    guild: Optional[discord.Guild],
    user: object,
    vc_channel: Optional[object] = None,
) -> Optional[object]:
    """Resolve a user's active voice channel with voice_states fallback when member cache is absent."""
    author_voice = getattr(user, "voice", None)
    author_ch = getattr(author_voice, "channel", None)
    author_ch_id = getattr(author_ch, "id", None)
    if author_ch is not None and isinstance(author_ch_id, int) and not isinstance(author_ch_id, bool):
        return author_ch

    user_id = getattr(user, "id", None)
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return None

    if vc_channel is not None:
        vc_vs = getattr(vc_channel, "voice_states", None)
        if isinstance(vc_vs, dict) and user_id in vc_vs:
            st_ch = getattr(vc_vs[user_id], "channel", None)
            st_ch_id = getattr(st_ch, "id", None)
            if st_ch is not None and isinstance(st_ch_id, int) and not isinstance(st_ch_id, bool):
                return st_ch
            return vc_channel

    if guild is not None:
        guild_vs = getattr(guild, "_voice_states", None)
        if isinstance(guild_vs, dict) and user_id in guild_vs:
            st_ch = getattr(guild_vs[user_id], "channel", None)
            st_ch_id = getattr(st_ch, "id", None)
            if st_ch is not None and isinstance(st_ch_id, int) and not isinstance(st_ch_id, bool):
                return st_ch

    return None


def count_non_bot_users_in_vc(
    bot: commands.Bot,
    guild: Optional[discord.Guild],
    vc_channel: object,
) -> int:
    """Count human listeners in VC using voice_states first to avoid uncached-member disconnects."""
    bot_id = getattr(getattr(bot, "user", None), "id", None)
    vc_vs = getattr(vc_channel, "voice_states", None)
    if isinstance(vc_vs, dict):
        count = 0
        get_member = getattr(guild, "get_member", None) if guild is not None else None
        for uid in vc_vs:
            if isinstance(bot_id, int) and not isinstance(bot_id, bool) and uid == bot_id:
                continue
            if callable(get_member):
                member = get_member(uid)
                if member is not None:
                    is_bot = getattr(member, "bot", None)
                    if isinstance(is_bot, bool) and is_bot:
                        continue
            count += 1
        return count

    members = getattr(vc_channel, "members", [])
    if not isinstance(members, (list, tuple, set)):
        return 0
    return sum(1 for m in members if not (isinstance(getattr(m, "bot", False), bool) and getattr(m, "bot", False)))


@dataclass
class TtsJob:
    """Single queued voice playback unit (synthesized text or local audio file)."""

    text: Optional[str] = None
    voice_id: str = ""
    user_id: Optional[int] = None
    rate_pct: int = 0
    pitch_hz: int = 0
    fallback_voice: Optional[str] = None
    local_file_path: Optional[Path] = None
    label: str = "TTS"


def _write_bytes_sync(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _unlink_with_retry_sync(path: Path) -> None:
    for attempt in range(4):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt < 3:
                time.sleep(0.05)
        except OSError:
            return


class GuildVoicePipeline:
    """Per-guild 2-stage async TTS pipeline (synthesizer worker -> FFmpeg player worker)."""

    def __init__(
        self,
        bot: commands.Bot,
        guild_id: int,
        synthesizer: tts_engine.HybridTtsSynthesizer,
        volume_boost: float,
        logger: logging.Logger,
        f5_max_chars: int = 200,
    ):
        self.bot = bot
        self.guild_id = guild_id
        self.synthesizer = synthesizer
        self.volume_boost = max(0.2, min(3.0, float(volume_boost)))
        self.f5_max_chars = max(20, min(1000, int(f5_max_chars)))
        self.logger = logger
        self.text_queue: asyncio.Queue[Optional[TtsJob]] = asyncio.Queue(maxsize=24)
        self.play_queue: asyncio.Queue[Optional[tuple[Path, bool, str, int]]] = asyncio.Queue(maxsize=8)
        self._generation: int = 0
        self._synth_task: Optional[asyncio.Task] = None
        self._play_task: Optional[asyncio.Task] = None

    def ensure_started(self) -> None:
        if self._synth_task is None or self._synth_task.done():
            self._synth_task = asyncio.create_task(self._synth_loop(), name=f"tts-synth-{self.guild_id}")
        if self._play_task is None or self._play_task.done():
            self._play_task = asyncio.create_task(self._play_loop(), name=f"tts-play-{self.guild_id}")

    def _get_connected_vc(self) -> Optional[discord.VoiceClient]:
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return None
        vc = guild.voice_client or discord.utils.get(self.bot.voice_clients, guild=guild)
        if vc and vc.is_connected():
            return vc
        return None

    def enqueue(self, job: TtsJob) -> bool:
        self.ensure_started()
        if self.text_queue.full():
            return False
        self.text_queue.put_nowait(job)
        return True

    async def _synth_loop(self) -> None:
        tmp_dir = Path(tempfile.gettempdir())
        while True:
            job = await self.text_queue.get()
            job_gen = self._generation
            try:
                if job is None:
                    break

                if job.local_file_path is not None:
                    if (
                        job_gen == self._generation
                        and await asyncio.to_thread(job.local_file_path.is_file)
                        and self._get_connected_vc()
                    ):
                        await self.play_queue.put((job.local_file_path, False, job.label, job_gen))
                    continue

                if not job.text:
                    continue

                text_to_speak = job.text
                is_f5 = job.voice_id.startswith("f5:")
                if is_f5:
                    text_to_speak = tts_engine.truncate_for_tts(text_to_speak, self.f5_max_chars)

                is_en = tts_engine.is_latin_dominant(text_to_speak)
                if is_f5:
                    chunk_limit = 95 if is_en else 42
                    first_limit = 65 if is_en else 25
                else:
                    chunk_limit = 135 if is_en else 45
                    first_limit = 70 if is_en else 28

                chunks = tts_engine.split_into_speech_chunks(
                    text_to_speak,
                    max_chunk_chars=chunk_limit,
                    first_chunk_max_chars=first_limit,
                )
                active_voice_id = job.voice_id
                for chunk in chunks:
                    if job_gen != self._generation or not self._get_connected_vc():
                        break
                    try:
                        audio_bytes, ext, used_fb = await self.synthesizer.synthesize(
                            chunk,
                            active_voice_id,
                            user_id=job.user_id,
                            rate_pct=job.rate_pct,
                            pitch_hz=job.pitch_hz,
                            fallback_voice=job.fallback_voice,
                        )
                        if used_fb:
                            if active_voice_id.startswith("f5:"):
                                active_voice_id = job.fallback_voice or self.synthesizer.default_voice
                            self.logger.info(f"🔄 [{job.label}] 主聲線暫時異常，已自動改用備援聲線播放")
                    except Exception as exc:
                        self.logger.error(f"❌ 語音合成失敗 ({job.label}): {exc}")
                        continue

                    tmp_path = tmp_dir / f"dgbot_tts_{self.guild_id}_{uuid.uuid4().hex}.{ext}"
                    try:
                        await asyncio.to_thread(_write_bytes_sync, tmp_path, audio_bytes)
                        if job_gen != self._generation or not self._get_connected_vc():
                            await asyncio.to_thread(_unlink_with_retry_sync, tmp_path)
                            break
                        await self.play_queue.put((tmp_path, True, job.label, job_gen))
                    except asyncio.CancelledError:
                        try:
                            _unlink_with_retry_sync(tmp_path)
                        except OSError:
                            pass
                        raise
                    except Exception as exc:
                        await asyncio.to_thread(_unlink_with_retry_sync, tmp_path)
                        self.logger.error(f"❌ 寫入暫存音檔失敗 ({job.label}): {exc}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error(f"❌ TTS 合成排程異常 (guild={self.guild_id}): {exc}", exc_info=True)
            finally:
                self.text_queue.task_done()

    async def _play_loop(self) -> None:
        while True:
            item = await self.play_queue.get()
            try:
                if item is None:
                    break
                file_path, delete_after, label, item_gen = item
                try:
                    if item_gen != self._generation:
                        continue
                    vc = self._get_connected_vc()
                    if not vc or not await asyncio.to_thread(file_path.is_file):
                        continue

                    while vc.is_connected() and vc.is_playing() and item_gen == self._generation:
                        await asyncio.sleep(0.05)

                    if item_gen != self._generation or not vc.is_connected():
                        continue

                    play_finished = asyncio.Event()

                    def after_playing(err: Optional[Exception]) -> None:
                        if err:
                            self.logger.error(f"❌ 語音播放錯誤 ({label}): {err}")
                        try:
                            self.bot.loop.call_soon_threadsafe(play_finished.set)
                        except RuntimeError:
                            pass

                    ffmpeg_opts = (
                        f'-vn -filter:a "volume={self.volume_boost:.2f},alimiter=limit=0.92:attack=5:release=50"'
                        if delete_after
                        else "-vn"
                    )
                    audio_source = discord.FFmpegPCMAudio(str(file_path), options=ffmpeg_opts)
                    self.logger.info(f"🔊 語音播放中 ({label})...")
                    try:
                        vc.play(audio_source, after=after_playing)
                    except Exception:
                        try:
                            audio_source.cleanup()
                        except Exception:
                            pass
                        raise

                    try:
                        await asyncio.wait_for(play_finished.wait(), timeout=120.0)
                    except asyncio.TimeoutError:
                        self.logger.warning(f"⚠️ 語音播放逾時 ({label})，已強制中止目前音訊")
                        try:
                            vc.stop()
                        except Exception:
                            pass
                        try:
                            audio_source.cleanup()
                        except Exception:
                            pass
                finally:
                    if delete_after:
                        try:
                            await asyncio.to_thread(_unlink_with_retry_sync, file_path)
                        except BaseException:
                            _unlink_with_retry_sync(file_path)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error(f"❌ TTS 播放排程異常 (guild={self.guild_id}): {exc}", exc_info=True)
            finally:
                self.play_queue.task_done()

    def clear_pending(self) -> None:
        self._generation += 1
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
                self.text_queue.task_done()
            except asyncio.QueueEmpty:
                break
        while not self.play_queue.empty():
            try:
                queued = self.play_queue.get_nowait()
                if queued is not None:
                    q_path, delete_after, _, _ = queued
                    if delete_after and q_path.exists():
                        _unlink_with_retry_sync(q_path)
                self.play_queue.task_done()
            except asyncio.QueueEmpty:
                break
        vc = self._get_connected_vc()
        if vc and vc.is_playing():
            vc.stop()

    async def stop(self) -> None:
        self.clear_pending()
        for task in (self._synth_task, self._play_task):
            if task and not task.done():
                task.cancel()
        for task in (self._synth_task, self._play_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._synth_task = None
        self._play_task = None
