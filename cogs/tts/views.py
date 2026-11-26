"""Interactive Discord UI Views for voice selection and voice clone moderation."""

from __future__ import annotations

import asyncio
import io
import math
from typing import TYPE_CHECKING, Optional

import discord

from cogs.tts.pipeline import TtsJob
from core.config import resolve_bot_display_name
from engines import tts_engine

if TYPE_CHECKING:
    from cogs.tts_cog import TtsCog


def build_voice_select_options(
    current_voice_id: str,
    f5_voices: list[dict],
    *,
    prefer_japanese: bool = False,
    f5_public_only: bool = False,
) -> tuple[list[discord.SelectOption], list[discord.SelectOption]]:
    """Build Edge-TTS and F5-TTS dropdown options with the active voice marked as default."""
    edge_items = list(tts_engine.EDGE_VOICES.items())
    if prefer_japanese:
        edge_items.sort(key=lambda kv: 0 if kv[1].get("category") == "ja-JP" else 1)

    edge_options = [
        discord.SelectOption(
            label=meta["label"][:100],
            value=vid,
            description=meta["description"][:100],
            default=(vid == current_voice_id),
        )
        for vid, meta in edge_items[:25]
    ]

    f5_options: list[discord.SelectOption] = []
    for fv in f5_voices[:25]:
        if f5_public_only:
            tag = "公開"
            desc_text = fv.get("description") or "公開特色聲線"
        else:
            tag = "公開" if fv.get("public") else "專屬授權"
            desc_text = fv.get("description") or f"{tag}特色聲線"
        f5_options.append(
            discord.SelectOption(
                label=f"🎙️ {fv['name']}"[:100],
                value=fv["id"][:100],
                description=f"[{tag}] {desc_text}"[:100],
                default=(fv["id"] == current_voice_id),
            )
        )
    return edge_options, f5_options


async def send_voice_preview(
    cog: "TtsCog",
    interaction: discord.Interaction,
    *,
    sample_text: str,
    voice_id: str,
    user_id: Optional[int],
    rate_pct: int,
    pitch_hz: int,
    fallback_voice: str,
    job_label: str,
    vc_queued_msg: str,
    followup_prefix: str,
    filename_prefix: str,
    log_context: str,
) -> None:
    """Shared preview handler: enqueue into active VC if connected, else return ephemeral audio."""
    vc = cog._get_connected_vc(interaction.guild) if interaction.guild else None
    if interaction.guild and vc is not None:
        err_msg = await cog._check_vc_control_permission(interaction.guild, interaction.user, vc)
        if err_msg:
            await interaction.response.send_message(err_msg, ephemeral=True)
            return

    rem = cog._check_preview_cooldown(interaction.user.id)
    if rem is not None:
        await interaction.response.send_message(
            f"⏱️ 試聽過於頻繁，請稍候 **{max(1, int(math.ceil(rem)))} 秒** 後再試。",
            ephemeral=True,
        )
        return

    if interaction.guild and vc is not None:
        pipeline = cog._get_or_create_pipeline(interaction.guild.id)
        ok = pipeline.enqueue(
            TtsJob(
                text=sample_text,
                voice_id=voice_id,
                user_id=user_id,
                rate_pct=rate_pct,
                pitch_hz=pitch_hz,
                fallback_voice=fallback_voice,
                label=job_label,
            )
        )
        msg = vc_queued_msg if ok else "⚠️ 語音佇列已滿，請稍後再試。"
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        audio_bytes, ext, used_fb = await cog.synthesizer.synthesize(
            sample_text,
            voice_id,
            user_id=user_id,
            rate_pct=rate_pct,
            pitch_hz=pitch_hz,
            fallback_voice=fallback_voice,
        )
        fb_note = "\n（特色聲線服務暫時不可用，已自動改用預設聲線試聽）" if used_fb else ""
        await interaction.followup.send(
            content=f"{followup_prefix}{fb_note}",
            file=discord.File(io.BytesIO(audio_bytes), filename=f"{filename_prefix}.{ext}"),
            ephemeral=True,
        )
    except Exception as exc:
        cog.logger.error(f"❌ {log_context}試聽合成失敗 (voice={voice_id}): {exc}", exc_info=True)
        await interaction.followup.send("❌ 語音試聽暫時無法產生，請稍後再試。", ephemeral=True)


def _sample_text_for_voice(voice_id: str, default_text: str, hk_text: str) -> str:
    vid_low = voice_id.lower()
    if "ja-jp" in vid_low:
        return "こんにちは、音声テストです。"
    if "zh-hk" in vid_low:
        return hk_text
    return default_text


class _BaseVoiceSelectView(discord.ui.View):
    """Shared timeout, owner check, and dropdown wiring for voice selection views."""

    _not_owner_error = "❌ 這不是你的設定選單。"

    def __init__(self, cog: "TtsCog", owner_id: int, *, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(self._not_owner_error, ephemeral=True)
            return False
        return True

    def _attach_voice_dropdowns(
        self,
        current_voice_id: str,
        f5_voices: list[dict],
        *,
        edge_placeholder: str,
        f5_placeholder: str,
        prefer_japanese: bool = False,
        f5_public_only: bool = False,
    ) -> None:
        edge_options, f5_options = build_voice_select_options(
            current_voice_id,
            f5_voices,
            prefer_japanese=prefer_japanese,
            f5_public_only=f5_public_only,
        )
        edge_select = discord.ui.Select(
            placeholder=edge_placeholder,
            min_values=1,
            max_values=1,
            options=edge_options,
            row=0,
        )
        edge_select.callback = self.on_select_edge
        self.add_item(edge_select)

        if f5_options:
            f5_select = discord.ui.Select(
                placeholder=f5_placeholder,
                min_values=1,
                max_values=1,
                options=f5_options,
                row=1,
            )
            f5_select.callback = self.on_select_f5
            self.add_item(f5_select)

    async def _apply_voice_choice(self, interaction: discord.Interaction, voice_id: str) -> None:
        raise NotImplementedError

    async def on_select_edge(self, interaction: discord.Interaction) -> None:
        await self._apply_voice_choice(interaction, interaction.data["values"][0])

    async def on_select_f5(self, interaction: discord.Interaction) -> None:
        await self._apply_voice_choice(interaction, interaction.data["values"][0])


class VoiceSelectView(_BaseVoiceSelectView):
    """Interactive personal voice & speed/pitch selector open to all users (no clone upload)."""

    _not_owner_error = "❌ 這不是你的聲線設定選單，請自己輸入 `!voice` 開啟選單。"

    def __init__(
        self,
        cog: "TtsCog",
        owner_id: int,
        pref: dict,
        f5_voices: list[dict],
    ):
        self.pref = dict(pref)
        self.f5_voices = list(f5_voices)
        super().__init__(cog, owner_id)
        self._rebuild_items()

    def build_embed(self) -> discord.Embed:
        cur_label = tts_engine.get_voice_display_label(self.pref["voice_id"], self.f5_voices)
        rate_str = tts_engine.format_edge_rate(self.pref["rate_pct"])
        pitch_str = tts_engine.format_edge_pitch(self.pref["pitch_hz"])
        mode_note = "自訂設定" if self.pref.get("is_custom") else "預設聲線"
        mute_note = (
            "🔇 已關閉（不會朗讀你在文字區打的字）"
            if self.pref.get("proxy_muted")
            else "🔊 開啟中"
        )
        if getattr(self.cog.settings, "enable_user_voice_clone", False):
            if getattr(self.cog.settings, "tts_clone_approval_mode", "auto") == "manual":
                clone_note = (
                    "支援 `!voiceclone` 申請專屬克隆聲線"
                    "（夾帶 3～15 秒清晰音檔＋逐字稿，待擁有者審核通過後啟用；輸入 `!voiceclone` 查看教學）"
                )
            else:
                clone_note = (
                    "支援 `!voiceclone` 自助建立私人克隆聲線"
                    "（夾帶 3～15 秒清晰音檔＋逐字稿，安檢通過立即啟用；輸入 `!voiceclone` 查看教學）"
                )
        else:
            clone_note = "想使用自訂角色聲線？可輸入 `!voiceclone` 查看申請與聯絡說明"

        embed = discord.Embed(
            title="🎙️ 個人語音聲線設定",
            description=(
                f"• **目前聲線**：{cur_label}（{mode_note}）\n"
                f"• **語速 / 音高**：`{rate_str}` / `{pitch_str}`\n"
                f"• **自動代說朗讀**：{mute_note}\n"
                f"• **專屬語音克隆**：{clone_note}\n"
                "• **微調指令**：`!voice rate <-50..+50>`、`!voice pitch <-30..+30>`、`!voice off` / `!voice on`、`!voice reset`"
            ),
            color=0x38BDF8,
        )
        if self.f5_voices:
            embed.set_footer(text=f"⚡ 上排為內建標準聲線；下排為目前開放的 {len(self.f5_voices)} 組特色聲線")
        else:
            embed.set_footer(text="⚡ 內建多國標準聲線，點選即刻生效")
        return embed

    def _rebuild_items(self) -> None:
        self.clear_items()
        self._attach_voice_dropdowns(
            self.pref["voice_id"],
            self.f5_voices,
            edge_placeholder="⚡ 選擇內建標準聲線",
            f5_placeholder="🎙️ 選擇特色／專屬聲線",
        )

        btn_reset = discord.ui.Button(
            label="🔄 恢復預設聲線",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        btn_reset.callback = self.on_reset
        self.add_item(btn_reset)

        btn_preview = discord.ui.Button(
            label="🔊 試聽目前聲線",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        btn_preview.callback = self.on_preview
        self.add_item(btn_preview)

        is_muted = bool(self.pref.get("proxy_muted"))
        btn_toggle_mute = discord.ui.Button(
            label="🔊 恢復朗讀我的文字" if is_muted else "🔇 不要朗讀我的文字",
            style=discord.ButtonStyle.success if is_muted else discord.ButtonStyle.danger,
            row=2,
        )
        btn_toggle_mute.callback = self.on_toggle_mute
        self.add_item(btn_toggle_mute)

    async def on_toggle_mute(self, interaction: discord.Interaction) -> None:
        self.pref = await asyncio.to_thread(
            tts_engine.set_user_voice_pref,
            self.owner_id,
            proxy_muted=not bool(self.pref.get("proxy_muted")),
            default_voice=self.cog.settings.tts_default_voice,
        )
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _apply_voice_choice(self, interaction: discord.Interaction, voice_id: str) -> None:
        self.pref = await asyncio.to_thread(
            tts_engine.set_user_voice_pref,
            self.owner_id,
            voice_id=voice_id,
            default_voice=self.cog.settings.tts_default_voice,
        )
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_reset(self, interaction: discord.Interaction) -> None:
        await asyncio.to_thread(tts_engine.reset_user_voice_pref, self.owner_id)
        self.pref = await asyncio.to_thread(
            tts_engine.get_user_voice_pref,
            self.owner_id,
            self.cog.settings.tts_default_voice,
        )
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_preview(self, interaction: discord.Interaction) -> None:
        vid = self.pref["voice_id"]
        sample_text = _sample_text_for_voice(
            vid,
            default_text="你好，這是語音聲線測試。",
            hk_text="你好，呢個係語音聲線測試。",
        )
        label_str = tts_engine.get_voice_display_label(vid, self.f5_voices)
        await send_voice_preview(
            self.cog,
            interaction,
            sample_text=sample_text,
            voice_id=vid,
            user_id=self.owner_id,
            rate_pct=self.pref["rate_pct"],
            pitch_hz=self.pref["pitch_hz"],
            fallback_voice=self.cog.settings.tts_default_voice,
            job_label=f"試聽:{interaction.user.display_name}",
            vc_queued_msg="🔊 已加入語音頻道試聽佇列！",
            followup_prefix=f"🔊 **{label_str}** 試聽：",
            filename_prefix="voice_preview",
            log_context=f"個人聲線 (user={self.owner_id}) ",
        )


class BotVoiceSelectView(_BaseVoiceSelectView):
    """Interactive guild-wide Bot AI voice & speed/pitch selector (Edge-TTS + public F5-TTS only)."""

    _not_owner_error = "❌ 這不是你開啟的設定選單，請自行輸入 `!voice bot` 或 `!botvoice` 開啟選單。"

    def __init__(
        self,
        cog: "TtsCog",
        owner_id: int,
        guild_id: int,
        g_cfg: dict,
        public_f5_voices: list[dict],
        mode: str = "general",
        bot_name: Optional[str] = None,
    ):
        self.guild_id = guild_id
        self.g_cfg = dict(g_cfg)
        self.public_f5_voices = [fv for fv in public_f5_voices if fv.get("public")]
        self.mode = "jp" if mode == "jp" else "general"
        guild_obj = cog.bot.get_guild(guild_id) if hasattr(getattr(cog, "bot", None), "get_guild") else None
        self.bot_name = (bot_name or "").strip()[:24] or resolve_bot_display_name(
            getattr(cog, "bot", None),
            guild=guild_obj,
            settings=getattr(cog, "settings", None),
        )
        super().__init__(cog, owner_id)
        self._rebuild_items()

    def build_embed(self) -> discord.Embed:
        gen_label = tts_engine.get_voice_display_label(self.g_cfg["ai_voice_id"], self.public_f5_voices)
        gen_rate = tts_engine.format_edge_rate(self.g_cfg["ai_rate_pct"])
        gen_pitch = tts_engine.format_edge_pitch(self.g_cfg["ai_pitch_hz"])
        gen_on = (
            "開啟"
            if self.g_cfg["ai_read_general"]
            else ("僅限綁定對象頻道" if self.g_cfg["allowed_channels"] else "關閉")
        )
        gen_mark = " 👉（編輯中）" if self.mode == "general" else ""

        lines = [
            f"• **💬 一般對話聲線**{gen_mark}：{gen_label}｜語速 `{gen_rate}` / 音高 `{gen_pitch}`（自動朗讀：**{gen_on}**）"
        ]
        if self.cog.settings.enable_japanese_mode or self.mode == "jp":
            jp_label = tts_engine.get_voice_display_label(self.g_cfg["ai_jp_voice_id"], self.public_f5_voices)
            jp_rate = tts_engine.format_edge_rate(self.g_cfg["ai_jp_rate_pct"])
            jp_pitch = tts_engine.format_edge_pitch(self.g_cfg["ai_jp_pitch_hz"])
            jp_on = "開啟" if self.g_cfg["ai_read_jp"] else "關閉"
            jp_mark = " 👉（編輯中）" if self.mode == "jp" else ""
            lines.append(
                f"• **🌸 日語共學聲線**{jp_mark}：{jp_label}｜語速 `{jp_rate}` / 音高 `{jp_pitch}`（自動朗讀：**{jp_on}**）"
            )

        lines.append(
            "• **微調指令**：`!voice bot rate <-50..+50>`、`!voice bot pitch <-30..+30>`、`!voice bot jp ...`、`!voice bot reset`"
        )
        embed = discord.Embed(
            title=f"🤖 {self.bot_name} 的語音聲線設定"[:250],
            description="\n".join(lines),
            color=0xA855F7,
        )
        if self.public_f5_voices:
            embed.set_footer(
                text=f"⚡ 上排為內建標準聲線；下排為 {len(self.public_f5_voices)} 組公開特色聲線（不包含私人聲線）"
            )
        else:
            embed.set_footer(text=f"⚡ 全體成員皆可隨時切換 {self.bot_name} 的說話風格")
        return embed

    def _rebuild_items(self) -> None:
        self.clear_items()
        cur_vid = self.g_cfg["ai_jp_voice_id"] if self.mode == "jp" else self.g_cfg["ai_voice_id"]
        edge_ph = (
            f"🌸 設定 {self.bot_name}「日語共學」標準聲線"[:100]
            if self.mode == "jp"
            else f"🤖 設定 {self.bot_name}「一般對話」標準聲線"[:100]
        )
        self._attach_voice_dropdowns(
            cur_vid,
            self.public_f5_voices,
            edge_placeholder=edge_ph,
            f5_placeholder=f"🎙️ 設定 {self.bot_name} 公開特色聲線"[:100],
            prefer_japanese=(self.mode == "jp"),
            f5_public_only=True,
        )

        if self.cog.settings.enable_japanese_mode:
            toggle_label = "🌸 切換編輯：日語聲線" if self.mode == "general" else "💬 切換編輯：一般聲線"
            btn_toggle = discord.ui.Button(
                label=toggle_label,
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            btn_toggle.callback = self.on_toggle_mode
            self.add_item(btn_toggle)

        btn_reset = discord.ui.Button(
            label="🔄 恢復預設",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        btn_reset.callback = self.on_reset
        self.add_item(btn_reset)

        btn_preview = discord.ui.Button(
            label=f"🔊 試聽 {self.bot_name} 聲線"[:80],
            style=discord.ButtonStyle.primary,
            row=2,
        )
        btn_preview.callback = self.on_preview
        self.add_item(btn_preview)

    async def _apply_voice_choice(self, interaction: discord.Interaction, voice_id: str) -> None:
        field = "ai_jp_voice_id" if self.mode == "jp" else "ai_voice_id"
        self.g_cfg = await self.cog._update_guild_settings(self.guild_id, **{field: voice_id})
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_toggle_mode(self, interaction: discord.Interaction) -> None:
        self.mode = "jp" if self.mode == "general" else "general"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_reset(self, interaction: discord.Interaction) -> None:
        self.g_cfg = await asyncio.to_thread(
            tts_engine.reset_guild_ai_voice,
            self.guild_id,
            target=self.mode,
            default_ai_jp=self.cog.settings.tts_ai_auto_read_jp,
            default_ai_general=self.cog.settings.tts_ai_auto_read_general,
            default_ai_voice=self.cog.settings.tts_default_voice,
            default_ai_jp_voice=self.cog.settings.tts_jp_voice,
        )
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_preview(self, interaction: discord.Interaction) -> None:
        if self.mode == "jp":
            vid = self.g_cfg["ai_jp_voice_id"]
            rate_pct = self.g_cfg["ai_jp_rate_pct"]
            pitch_hz = self.g_cfg["ai_jp_pitch_hz"]
            fallback = self.cog.settings.tts_jp_voice
            sample_text = "こんにちは、私はあなたの日本語パートナーです。"
        else:
            vid = self.g_cfg["ai_voice_id"]
            rate_pct = self.g_cfg["ai_rate_pct"]
            pitch_hz = self.g_cfg["ai_pitch_hz"]
            fallback = self.cog.settings.tts_default_voice
            sample_text = _sample_text_for_voice(
                vid,
                default_text="你好，我是你的 AI 聊天夥伴，這是語音試聽。",
                hk_text="你好，我係你嘅 AI 聊天夥伴。",
            )

        label_str = tts_engine.get_voice_display_label(vid, self.public_f5_voices)
        await send_voice_preview(
            self.cog,
            interaction,
            sample_text=sample_text,
            voice_id=vid,
            user_id=None,
            rate_pct=rate_pct,
            pitch_hz=pitch_hz,
            fallback_voice=fallback,
            job_label=f"試聽:{self.bot_name}",
            vc_queued_msg=f"🔊 已將 {self.bot_name} 聲線試聽加入語音頻道佇列！",
            followup_prefix=f"🔊 {self.bot_name} 目前聲線（**{label_str}**）試聽：",
            filename_prefix="bot_voice_preview",
            log_context=f"機器人聲線 (guild={self.guild_id}) ",
        )


class CloneReviewView(discord.ui.View):
    """Persistent 1-click owner approval buttons delivered via DM for user voice clone requests."""

    def __init__(self, req_id: int, *, request_public: bool = True, disabled: bool = False):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="✅ 核准為私人聲線",
                style=discord.ButtonStyle.success,
                custom_id=f"vc_approve_priv:{req_id}",
                disabled=disabled,
            )
        )
        if request_public:
            self.add_item(
                discord.ui.Button(
                    label="🌐 核准為公開聲線",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"vc_approve_pub:{req_id}",
                    disabled=disabled,
                )
            )
        self.add_item(
            discord.ui.Button(
                label="❌ 拒絕申請",
                style=discord.ButtonStyle.danger,
                custom_id=f"vc_reject:{req_id}",
                disabled=disabled,
            )
        )


class CloneAuditView(discord.ui.View):
    """Persistent post-hoc owner moderation buttons for auto-approved user voice clones."""

    def __init__(
        self,
        voice_id: str,
        *,
        request_public: bool = False,
        promote_disabled: bool = False,
        all_disabled: bool = False,
    ):
        super().__init__(timeout=None)
        if request_public:
            self.add_item(
                discord.ui.Button(
                    label="🌐 允許成為公開聲線",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"vc_promote_pub:{voice_id}",
                    disabled=all_disabled or promote_disabled,
                )
            )
            self.add_item(
                discord.ui.Button(
                    label="🔒 維持私人（不公開）",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"vc_keep_priv:{voice_id}",
                    disabled=all_disabled or promote_disabled,
                )
            )
        self.add_item(
            discord.ui.Button(
                label="🗑️ 撤銷刪除此聲線",
                style=discord.ButtonStyle.danger,
                custom_id=f"vc_audit_revoke:{voice_id}",
                disabled=all_disabled,
            )
        )
