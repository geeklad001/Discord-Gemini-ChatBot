"""Voice TTS Cog: 0-VRAM Edge-TTS default/JP + optional lazy-load F5-TTS + 3-tier permissions."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from cogs.tts import (
    BotVoiceSelectView,
    CloneAuditView,
    CloneReviewView,
    CloneServiceMixin,
    GuildVoicePipeline,
    TtsAdminMixin,
    TtsJob,
    VoiceSelectView,
    VoiceSettingsMixin,
    count_non_bot_users_in_vc,
    resolve_user_voice_channel,
    sweep_orphaned_temp_files,
)
from core.config import BotSettings, is_bot_owner as check_bot_owner
from engines import tts_engine

__all__ = [
    "BotVoiceSelectView",
    "CloneAuditView",
    "CloneReviewView",
    "GuildVoicePipeline",
    "TtsCog",
    "TtsJob",
    "VoiceSelectView",
]


class TtsCog(VoiceSettingsMixin, CloneServiceMixin, TtsAdminMixin, commands.Cog):
    """Voice channel playback, human proxy TTS, AI speech hook, and 3-tier permission controls."""

    def __init__(self, bot: commands.Bot, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.settings = settings
        self.logger = logger
        self.synthesizer = tts_engine.HybridTtsSynthesizer(
            tts_api_url=settings.tts_api_url,
            default_voice=settings.tts_default_voice,
            jp_voice=settings.tts_jp_voice,
            api_token=settings.tts_api_token,
        )
        self.pipelines: dict[int, GuildVoicePipeline] = {}
        self._enqueue_locks: dict[int, asyncio.Lock] = {}
        self._vc_connect_locks: dict[int, asyncio.Lock] = {}
        self._clone_user_locks: dict[int, asyncio.Lock] = {}
        self._clone_review_locks: dict[int, asyncio.Lock] = {}
        self._clone_global_lock: Optional[asyncio.Lock] = None
        self._clone_ffmpeg_sem: Optional[asyncio.Semaphore] = None
        self._clone_public_cooldowns: dict[int, float] = {}
        self._preview_cooldowns: dict[int, float] = {}
        self._sweep_orphaned_temp_files()
        try:
            import davey as _davey

            discord_ver = str(getattr(discord, "__version__", ""))
            davey_ver = str(getattr(_davey, "__version__", ""))
            if discord_ver == "2.7.1" and davey_ver.startswith("0.2."):
                self.logger.warning(
                    "⚠️ 偵測到 discord.py 2.7.1 搭配 davey %s，可能會導致語音頻道顯示綠圈卻無聲音！"
                    '請執行: pip install "davey>=0.1.6,<0.2.0"',
                    davey_ver,
                )
        except Exception:
            pass

    _sweep_orphaned_temp_files = staticmethod(sweep_orphaned_temp_files)

    async def cog_unload(self) -> None:
        for guild_id in list(self.pipelines.keys()):
            await self._cleanup_guild_pipeline(guild_id)
        await self.synthesizer.close()

    # -------------------------------------------------------------
    # Permission Helpers (3-Tier: General User / Guild Admin / Bot Owner)
    # -------------------------------------------------------------
    async def is_bot_owner(self, user: discord.abc.User) -> bool:
        return await check_bot_owner(self.bot, self.settings, user)

    @staticmethod
    def _has_guild_admin_perms(user: object) -> bool:
        perms = getattr(user, "guild_permissions", None)
        return bool(
            perms is not None
            and (
                getattr(perms, "administrator", False)
                or getattr(perms, "manage_guild", False)
                or getattr(perms, "manage_channels", False)
            )
        )

    async def is_tts_admin(self, ctx: commands.Context) -> bool:
        if await self.is_bot_owner(ctx.author):
            return True
        if ctx.guild is None:
            return False
        return self._has_guild_admin_perms(ctx.author)

    # -------------------------------------------------------------
    # Pipeline, Lock, Guild Settings & VC Helpers
    # -------------------------------------------------------------
    @staticmethod
    def _clip_reply(text: str, limit: int = 1900) -> str:
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 12)] + "\n...（已截斷）"

    def _get_raw_vc(self, guild: Optional[discord.Guild]) -> Optional[discord.VoiceClient]:
        if not guild:
            return None
        return getattr(guild, "voice_client", None) or discord.utils.get(self.bot.voice_clients, guild=guild)

    def _get_connected_vc(self, guild: Optional[discord.Guild]) -> Optional[discord.VoiceClient]:
        vc = self._get_raw_vc(guild)
        if vc and vc.is_connected():
            return vc
        return None

    async def _cleanup_stale_vc(self, guild: Optional[discord.Guild]) -> Optional[discord.VoiceClient]:
        if not guild:
            return None
        raw_vc = self._get_raw_vc(guild)
        if raw_vc is not None and not raw_vc.is_connected():
            await self._cleanup_guild_pipeline(guild.id)
            try:
                await raw_vc.disconnect(force=True)
            except Exception as exc:
                self.logger.warning(f"⚠️ 清理斷線殘留 VoiceClient 失敗 (guild={guild.id}): {exc}")
            return None
        return raw_vc

    def _get_keyed_lock(self, attr_name: str, key: int) -> asyncio.Lock:
        locks = getattr(self, attr_name, None)
        if locks is None:
            locks = {}
            setattr(self, attr_name, locks)
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    def _get_enqueue_lock(self, guild_id: int) -> asyncio.Lock:
        return self._get_keyed_lock("_enqueue_locks", guild_id)

    def _get_vc_connect_lock(self, guild_id: int) -> asyncio.Lock:
        return self._get_keyed_lock("_vc_connect_locks", guild_id)

    def _check_preview_cooldown(self, user_id: int, cooldown_sec: float = 5.0) -> Optional[float]:
        cooldowns = getattr(self, "_preview_cooldowns", None)
        if cooldowns is None:
            cooldowns = {}
            self._preview_cooldowns = cooldowns
        now = time.monotonic()
        last = cooldowns.get(user_id)
        if last is not None and (now - last) < cooldown_sec:
            return max(0.5, cooldown_sec - (now - last))
        cooldowns[user_id] = now
        return None

    async def _check_vc_control_permission(
        self,
        guild: discord.Guild,
        user: discord.abc.User,
        vc: discord.VoiceClient,
    ) -> Optional[str]:
        g_cfg = await asyncio.to_thread(self._get_guild_settings, guild.id)
        if not g_cfg.get("require_in_vc"):
            return None
        if await self.is_bot_owner(user) or self._has_guild_admin_perms(user):
            return None
        vc_ch = getattr(vc, "channel", None)
        author_ch = self._resolve_user_voice_channel(guild, user, vc_channel=vc_ch)
        if author_ch is None or getattr(author_ch, "id", None) != getattr(vc_ch, "id", None):
            vc_name = str(getattr(vc_ch, "name", "語音頻道"))[:60]
            return f"🔒 目前已開啟同房限制，請先加入 **{vc_name}** 再進行此操作（管理員可略過此限制）。"
        return None

    def is_connected_in_guild(self, guild_id: int) -> bool:
        guild = self.bot.get_guild(guild_id)
        return self._get_connected_vc(guild) is not None

    _resolve_user_voice_channel = staticmethod(resolve_user_voice_channel)

    def _count_non_bot_users_in_vc(self, guild: Optional[discord.Guild], vc_channel: object) -> int:
        return count_non_bot_users_in_vc(self.bot, guild, vc_channel)

    def _get_or_create_pipeline(self, guild_id: int) -> GuildVoicePipeline:
        pipeline = self.pipelines.get(guild_id)
        if pipeline is None:
            pipeline = GuildVoicePipeline(
                bot=self.bot,
                guild_id=guild_id,
                synthesizer=self.synthesizer,
                volume_boost=self.settings.tts_volume_boost,
                logger=self.logger,
                f5_max_chars=self.settings.tts_f5_max_chars,
            )
            self.pipelines[guild_id] = pipeline
        return pipeline

    async def _cleanup_guild_pipeline(self, guild_id: int) -> None:
        pipeline = self.pipelines.pop(guild_id, None)
        if pipeline is not None:
            await pipeline.stop()

    def _get_guild_settings(self, guild_id: int) -> dict:
        return tts_engine.get_guild_tts_settings(
            guild_id,
            default_ai_jp=self.settings.tts_ai_auto_read_jp,
            default_ai_general=self.settings.tts_ai_auto_read_general,
            default_ai_voice=self.settings.tts_default_voice,
            default_ai_jp_voice=self.settings.tts_jp_voice,
        )

    async def _update_guild_settings(self, guild_id: int, **fields) -> dict:
        return await asyncio.to_thread(
            tts_engine.update_guild_tts_settings,
            guild_id,
            default_ai_jp=self.settings.tts_ai_auto_read_jp,
            default_ai_general=self.settings.tts_ai_auto_read_general,
            default_ai_voice=self.settings.tts_default_voice,
            default_ai_jp_voice=self.settings.tts_jp_voice,
            **fields,
        )

    @staticmethod
    def _is_channel_ignored(g_cfg: dict, channel: object) -> bool:
        if channel is None:
            return False
        cid = getattr(channel, "id", None)
        parent_id = getattr(channel, "parent_id", None)
        ignored = g_cfg["ignored_channels"]
        return (cid in ignored) or (parent_id is not None and parent_id in ignored)

    def _is_channel_tts_target(self, guild: discord.Guild, g_cfg: dict, channel: object) -> bool:
        if channel is None:
            return False
        cid = getattr(channel, "id", None)
        parent_id = getattr(channel, "parent_id", None)
        allowed = g_cfg["allowed_channels"]
        if (cid in allowed) or (parent_id is not None and parent_id in allowed):
            return True

        # 情侶共學模式：當開啟 AI 日語朗讀時，日語共學雙頻道（target_channel_id 與 tts_channel_id）
        # 自動同步支援真人打字代說，不會發生「只唸機器人日文、不唸使用者訊息」的不對稱狀況
        if self.settings.enable_japanese_mode and g_cfg.get("ai_read_jp", True):
            jp_channels = {x for x in (self.settings.target_channel_id, self.settings.tts_channel_id) if x}
            if (cid in jp_channels) or (parent_id is not None and parent_id in jp_channels):
                return True

        if allowed:
            return False
        vc = self._get_connected_vc(guild)
        if not vc or not vc.channel or cid != vc.channel.id:
            return False
        ai_channels = {
            x
            for x in (
                self.settings.target_channel_id,
                self.settings.tts_channel_id,
                self.settings.general_channel_id,
            )
            if x
        }
        return cid not in ai_channels

    # -------------------------------------------------------------
    # Public Hooks for chat_pipeline & flashcard_cog & bot.on_message
    # -------------------------------------------------------------
    async def enqueue_ai_speech(
        self,
        guild: Optional[discord.Guild],
        text: str,
        *,
        is_japanese: bool = True,
        force: bool = False,
        channel: Optional[object] = None,
    ) -> bool:
        if not guild:
            return False
        async with self._get_enqueue_lock(guild.id):
            if not self.is_connected_in_guild(guild.id):
                return False
            g_cfg = await asyncio.to_thread(self._get_guild_settings, guild.id)
            if not force:
                if self._is_channel_ignored(g_cfg, channel):
                    self.logger.info(
                        f"⏭️ [TTS AI 略過] 頻道 #{getattr(channel, 'name', getattr(channel, 'id', '?'))} 在忽略清單內"
                    )
                    return False
                in_target = self._is_channel_tts_target(guild, g_cfg, channel)
                if is_japanese and not (g_cfg["ai_read_jp"] or in_target):
                    self.logger.info("⏭️ [TTS AI 略過] AI 日語自動朗讀未開啟且非對象頻道")
                    return False
                if not is_japanese and not (g_cfg["ai_read_general"] or in_target):
                    self.logger.info("⏭️ [TTS AI 略過] AI 一般對話自動朗讀未開啟且非綁定對象頻道")
                    return False

            cleaned = tts_engine.clean_text_for_tts(text, max_chars=self.settings.tts_max_chars)
            if not cleaned:
                self.logger.info(f"⏭️ [TTS AI 略過] 回覆過濾後無可朗讀文字 (原長={len(text or '')})")
                return False

            if is_japanese:
                voice_id = g_cfg["ai_jp_voice_id"]
                rate_pct = g_cfg["ai_jp_rate_pct"]
                pitch_hz = g_cfg["ai_jp_pitch_hz"]
                fallback_voice = self.settings.tts_jp_voice
            else:
                voice_id = g_cfg["ai_voice_id"]
                rate_pct = g_cfg["ai_rate_pct"]
                pitch_hz = g_cfg["ai_pitch_hz"]
                fallback_voice = self.settings.tts_default_voice

            label = "AI 日語" if is_japanese else "AI 對話"
            pipeline = self._get_or_create_pipeline(guild.id)
            enqueued = pipeline.enqueue(
                TtsJob(
                    text=cleaned,
                    voice_id=voice_id,
                    user_id=None,
                    rate_pct=rate_pct,
                    pitch_hz=pitch_hz,
                    fallback_voice=fallback_voice,
                    label=label,
                )
            )
            if enqueued:
                self.logger.info(
                    f"🤖 [TTS AI] 已排入朗讀 ({label} | {voice_id} | rate={rate_pct:+d}% pitch={pitch_hz:+d}Hz): '{cleaned[:40]}'"
                )
            else:
                self.logger.warning(f"⚠️ [TTS AI 略過] 語音佇列已滿，略過 ({label}): '{cleaned[:40]}'")
            return enqueued

    async def enqueue_local_audio_file(
        self,
        guild: Optional[discord.Guild],
        file_path: Path,
        *,
        label: str = "Anki",
    ) -> bool:
        path = Path(file_path)
        if not guild or not self.is_connected_in_guild(guild.id) or not path.is_file():
            return False
        pipeline = self._get_or_create_pipeline(guild.id)
        return pipeline.enqueue(TtsJob(local_file_path=path, label=label))

    async def synthesize_ephemeral_jp_audio(
        self,
        text: str,
        guild_id: Optional[int] = None,
    ) -> Optional[tuple[bytes, str]]:
        cleaned = tts_engine.clean_text_for_tts(text, max_chars=self.settings.tts_max_chars)
        if not cleaned:
            return None
        if guild_id is not None:
            g_cfg = await asyncio.to_thread(self._get_guild_settings, guild_id)
            voice_id = g_cfg["ai_jp_voice_id"]
            rate_pct = g_cfg["ai_jp_rate_pct"]
            pitch_hz = g_cfg["ai_jp_pitch_hz"]
        else:
            voice_id = self.settings.tts_jp_voice
            rate_pct = 0
            pitch_hz = 0
        audio_bytes, ext, _ = await self.synthesizer.synthesize(
            cleaned,
            voice_id,
            user_id=None,
            rate_pct=rate_pct,
            pitch_hz=pitch_hz,
            fallback_voice=self.settings.tts_jp_voice,
        )
        return audio_bytes, ext

    async def handle_proxy_message(self, message: discord.Message) -> bool:
        """Queue eligible user messages for voice-proxy TTS without blocking AI chat in target channels."""
        if message.author.bot or not message.guild:
            return False

        async with self._get_enqueue_lock(message.guild.id):
            vc = self._get_connected_vc(message.guild)
            if not vc or not vc.channel:
                return False

            g_cfg = await asyncio.to_thread(self._get_guild_settings, message.guild.id)
            if self._is_channel_ignored(g_cfg, message.channel):
                return False
            if not self._is_channel_tts_target(message.guild, g_cfg, message.channel):
                return False

            if not g_cfg["proxy_enabled"]:
                self.logger.info("⏭️ [TTS 略過] 伺服器已關閉真人打字代說 (proxy_enabled=False)")
                return False

            if g_cfg["require_in_vc"]:
                author_ch = self._resolve_user_voice_channel(message.guild, message.author, vc_channel=vc.channel)
                vc_ch_id = getattr(vc.channel, "id", None)
                author_ch_id = getattr(author_ch, "id", None)
                if author_ch is None or author_ch_id != vc_ch_id:
                    detected_vc = (
                        f"#{getattr(author_ch, 'name', author_ch_id)}"
                        if author_ch is not None
                        else "未在任何語音頻道"
                    )
                    self.logger.info(
                        f"⏭️ [TTS 略過] [{message.author.display_name}] (ID:{message.author.id}) 在 "
                        f"#{getattr(message.channel, 'name', getattr(message.channel, 'id', '?'))} 發言，"
                        f"因已開啟同房限制且偵測語音狀態為【{detected_vc}】（機器人在 #{getattr(vc.channel, 'name', vc_ch_id)}）"
                    )
                    return False

            pref = await asyncio.to_thread(
                tts_engine.get_user_voice_pref,
                message.author.id,
                self.settings.tts_default_voice,
            )
            if pref.get("proxy_muted"):
                self.logger.info(
                    f"⏭️ [TTS 略過] [{message.author.display_name}] (ID:{message.author.id}) "
                    "已開啟個人靜音代說 (proxy_muted=True)"
                )
                return False

            raw_content = getattr(message, "clean_content", None) or getattr(message, "content", "") or ""
            cleaned = tts_engine.clean_text_for_tts(raw_content, max_chars=self.settings.tts_max_chars)
            if not cleaned:
                self.logger.info(
                    f"⏭️ [TTS 略過] [{message.author.display_name}] 訊息過濾後無可朗讀文字 (原長={len(raw_content)})"
                )
                return False

            pipeline = self._get_or_create_pipeline(message.guild.id)
            enqueued = bool(
                pipeline.enqueue(
                    TtsJob(
                        text=cleaned,
                        voice_id=pref["voice_id"],
                        user_id=message.author.id,
                        rate_pct=pref["rate_pct"],
                        pitch_hz=pref["pitch_hz"],
                        fallback_voice=self.settings.tts_default_voice,
                        label=message.author.display_name,
                    )
                )
            )
            mode_tag = "自訂" if pref.get("is_custom") else "預設"
            if enqueued:
                self.logger.info(
                    f"🗣️ [TTS 代說] [{message.author.display_name}] ({mode_tag}:{pref['voice_id']}) "
                    f"已排入佇列: '{cleaned[:40]}'"
                )
            else:
                self.logger.warning(
                    f"⚠️ [TTS 略過] [{message.author.display_name}] 語音佇列已滿，略過: '{cleaned[:40]}'"
                )
            return enqueued

    # -------------------------------------------------------------
    # Event Listeners (Auto-Disconnect & Persistent Clone Review Buttons)
    # -------------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild
        if self.bot.user and member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                await self._cleanup_guild_pipeline(guild.id)
            return

        vc = self._get_connected_vc(guild)
        if not vc or not vc.channel:
            return
        if self._count_non_bot_users_in_vc(guild, vc.channel) <= 0:
            await self._cleanup_guild_pipeline(guild.id)
            try:
                await vc.disconnect()
                self.logger.info(f"👋 語音頻道 #{vc.channel.name} 已無成員，自動退出。")
            except Exception as exc:
                self.logger.warning(f"⚠️ 自動退出語音頻道失敗: {exc}")

    @commands.Cog.listener("on_interaction")
    async def _on_clone_review_interaction(self, interaction: discord.Interaction) -> None:
        await self._dispatch_clone_review_interaction(interaction)

    # -------------------------------------------------------------
    # Commands (Registered Directly on TtsCog)
    # -------------------------------------------------------------
    @commands.command(name="join", aliases=["進房", "語音"])
    async def join_command(self, ctx: commands.Context) -> None:
        """讓機器人加入你所在的語音頻道並啟動語音朗讀。"""
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return
        target_vc = self._resolve_user_voice_channel(ctx.guild, ctx.author)
        if target_vc is None:
            await ctx.send("❌ 請先加入一個語音頻道！")
            return

        async with self._get_vc_connect_lock(ctx.guild.id):
            vc = await self._cleanup_stale_vc(ctx.guild)
            if vc:
                if getattr(vc.channel, "id", None) != getattr(target_vc, "id", None):
                    try:
                        await vc.move_to(target_vc)
                    except Exception as exc:
                        self.logger.error(
                            f"❌ 移動語音頻道失敗 (guild={ctx.guild.id}, vc={getattr(target_vc, 'id', '?')}): {exc}",
                            exc_info=True,
                        )
                        await ctx.send("❌ 無法移動至該語音頻道，請確認機器人權限後再試。")
                        return
                    self._get_or_create_pipeline(ctx.guild.id).ensure_started()
                    await ctx.send(
                        self._clip_reply(f"🔄 已移動至語音頻道 **{str(target_vc.name)[:80]}**，語音朗讀已就緒！")
                    )
                else:
                    await ctx.send(self._clip_reply(f"✅ 我已經在 **{str(target_vc.name)[:80]}** 裡了！"))
                return

            try:
                await target_vc.connect()
            except Exception as exc:
                self.logger.error(
                    f"❌ 加入語音頻道失敗 (guild={ctx.guild.id}, vc={getattr(target_vc, 'id', '?')}): {exc}",
                    exc_info=True,
                )
                await ctx.send("❌ 無法加入語音頻道，請確認機器人擁有連線與說話權限後再試。")
                return

        self._get_or_create_pipeline(ctx.guild.id).ensure_started()
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        if g_cfg["allowed_channels"]:
            ch_hint = "已綁定對象頻道：" + self._format_channel_list(ctx.guild, g_cfg["allowed_channels"], "")
        else:
            ch_hint = f"目前預設監聽語音房內建文字區（`#{str(target_vc.name)[:80]}`）；在該文字區打字即可代說"
        await ctx.send(
            self._clip_reply(
                f"🎙️ 已加入 **{str(target_vc.name)[:80]}**！\n• {ch_hint}\n• 輸入 `!voice` 可選聲線、調語速音高"
            )
        )

    @commands.command(name="leave", aliases=["dc", "退房"])
    async def leave_command(self, ctx: commands.Context) -> None:
        """讓機器人退出語音頻道並停止所有排隊語音。"""
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return
        async with self._get_vc_connect_lock(ctx.guild.id):
            raw_vc = self._get_raw_vc(ctx.guild)
            if not raw_vc:
                await ctx.send("❌ 目前我不在任何語音頻道中。")
                return
            if not raw_vc.is_connected():
                await self._cleanup_guild_pipeline(ctx.guild.id)
                try:
                    await raw_vc.disconnect(force=True)
                except Exception as exc:
                    self.logger.warning(f"⚠️ 清理斷線殘留語音連線失敗 (guild={ctx.guild.id}): {exc}")
                await ctx.send("👋 已清理斷線殘留的語音連線並清空語音佇列。輸入 `!join` 可隨時再叫我進房。")
                return

            err_msg = await self._check_vc_control_permission(ctx.guild, ctx.author, raw_vc)
            if err_msg:
                await ctx.send(err_msg)
                return

            await self._cleanup_guild_pipeline(ctx.guild.id)
            try:
                await raw_vc.disconnect()
            except Exception:
                try:
                    await raw_vc.disconnect(force=True)
                except Exception as exc:
                    self.logger.warning(f"⚠️ 退出語音頻道失敗 (guild={ctx.guild.id}): {exc}")
            await ctx.send("👋 已退出語音頻道，並清空語音佇列。輸入 `!join` 可隨時再叫我進房。")

    @commands.command(name="say", aliases=["說", "朗讀"])
    async def say_command(self, ctx: commands.Context, *, text: Optional[str] = None) -> None:
        """在任意頻道使用 `!say <內容>` 立即代為朗讀（機器人已進房時預設未進房也可用）。"""
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return
        if not text or not text.strip():
            await ctx.send("💡 用法：`!say <要朗讀的文字>`")
            return

        vc = self._get_connected_vc(ctx.guild)
        if not vc:
            async with self._get_vc_connect_lock(ctx.guild.id):
                vc = await self._cleanup_stale_vc(ctx.guild)
                if not vc:
                    author_ch = self._resolve_user_voice_channel(ctx.guild, ctx.author)
                    if author_ch is None:
                        await ctx.send("❌ 目前我尚未加入語音頻道，請先進入語音頻道再使用 `!say`（或先由房內成員輸入 `!join`）。")
                        return
                    try:
                        await author_ch.connect()
                    except Exception as exc:
                        self.logger.error(
                            f"❌ !say 加入語音頻道失敗 (guild={ctx.guild.id}, vc={getattr(author_ch, 'id', '?')}): {exc}",
                            exc_info=True,
                        )
                        await ctx.send("❌ 無法加入語音頻道，請確認機器人擁有連線與說話權限後再試。")
                        return

        async with self._get_enqueue_lock(ctx.guild.id):
            vc = self._get_connected_vc(ctx.guild)
            if not vc:
                await ctx.send("❌ 目前語音連線尚未就緒，請稍後再試或重新輸入 `!join`。")
                return
            author_ch = self._resolve_user_voice_channel(ctx.guild, ctx.author, vc_channel=vc.channel)
            g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
            if g_cfg["require_in_vc"] and (
                author_ch is None or getattr(vc.channel, "id", None) != getattr(author_ch, "id", None)
            ):
                await ctx.send(
                    self._clip_reply(
                        f"❌ 目前已開啟同房限制，我正在 **{str(vc.channel.name)[:80]}** 服務中，請先進入同一個語音房再使用 `!say`。"
                    )
                )
                return

            cleaned = tts_engine.clean_text_for_tts(text, max_chars=self.settings.tts_max_chars)
            if not cleaned:
                await ctx.send("⚠️ 過濾後沒有可朗讀的有效文字。")
                return

            pref = await asyncio.to_thread(
                tts_engine.get_user_voice_pref,
                ctx.author.id,
                self.settings.tts_default_voice,
            )
            pipeline = self._get_or_create_pipeline(ctx.guild.id)
            ok = pipeline.enqueue(
                TtsJob(
                    text=cleaned,
                    voice_id=pref["voice_id"],
                    user_id=ctx.author.id,
                    rate_pct=pref["rate_pct"],
                    pitch_hz=pref["pitch_hz"],
                    fallback_voice=self.settings.tts_default_voice,
                    label=ctx.author.display_name,
                )
            )
            if not ok:
                await ctx.send("⚠️ 語音排隊已達上限，請稍候再試。")

    @commands.command(name="skip", aliases=["跳過"])
    async def skip_command(self, ctx: commands.Context) -> None:
        """跳過目前正在播放的語音段落。"""
        if not ctx.guild:
            return
        vc = self._get_connected_vc(ctx.guild)
        if not vc:
            await ctx.send("ℹ️ 目前沒有正在播放的語音。")
            return
        err_msg = await self._check_vc_control_permission(ctx.guild, ctx.author, vc)
        if err_msg:
            await ctx.send(err_msg)
            return
        if vc.is_playing():
            vc.stop()
            await ctx.send("⏭️ 已跳過目前語音。")
        else:
            await ctx.send("ℹ️ 目前沒有正在播放的語音。")

    @commands.command(name="voice", aliases=["聲音", "音色"])
    async def voice_command(
        self,
        ctx: commands.Context,
        sub: Optional[str] = None,
        *,
        arg: Optional[str] = None,
    ) -> None:
        """設定個人語音聲線、語速與音高（全體一般使用者皆可使用；私人克隆由主機管理）。"""
        await self._handle_voice_command(ctx, sub, arg)

    @commands.command(name="botvoice", aliases=["aivoice", "機器人聲音", "機器人聲線"])
    async def botvoice_command(self, ctx: commands.Context, *, arg: Optional[str] = None) -> None:
        """設定機器人自己的 AI 朗讀聲線、語速與音高（全體伺服器成員皆可使用）。"""
        await self._handle_bot_voice(ctx, arg)

    @commands.command(name="voiceclone", aliases=["clone", "克隆", "聲音克隆", "複製聲音"])
    async def voiceclone_command(self, ctx: commands.Context, *, _arg: Optional[str] = None) -> None:
        """語音克隆系統：依實例設定提供審核申請、個人槽位管理或擁有者聯絡說明。"""
        await self._handle_voiceclone(ctx, _arg)

    @commands.command(name="tts")
    async def tts_command(
        self,
        ctx: commands.Context,
        action: Optional[str] = None,
        *,
        arg: Optional[str] = None,
    ) -> None:
        """查看 TTS 狀態，或由管理員設定對象頻道與 AI 朗讀開關、擁有者控管 F5-TTS 顯存。"""
        await self._handle_tts_command(ctx, action, arg)
