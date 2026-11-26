"""Unified personal (!voice) and guild AI (!voice bot / !botvoice) voice settings handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import discord
from discord.ext import commands

from cogs.tts.views import BotVoiceSelectView, VoiceSelectView
from engines import tts_engine


@dataclass(frozen=True)
class _VoiceDispatchSpec:
    """Parameterized messages & persistence callbacks shared by personal and bot voice trees."""

    current_voice_id: str
    current_rate_pct: int
    current_pitch_hz: int
    voice_key: str
    rate_key: str
    pitch_key: str
    list_title: str
    list_f5_header: str
    list_color: int
    list_footer: str
    pitch_invalid_msg: str
    rate_invalid_msg: str
    pitch_info_prefix: str
    rate_info_prefix: str
    pitch_updated_prefix: str
    rate_updated_prefix: str
    switched_prefix: str
    not_found_hint: str
    clone_reject_msg: str
    reset_fn: Callable[[], Awaitable[str]]
    save_fn: Callable[..., Awaitable[dict]]


class VoiceSettingsMixin:
    """Mixin unifying !voice and !voice bot / !botvoice subcommand dispatch."""

    @staticmethod
    def _prosody_limited_hint(voice_id: str, value: int) -> str:
        if value != 0 and tts_engine.is_edge_prosody_limited(voice_id):
            return (
                "\nℹ️ 提醒：此聲線目前不支援微調音高／語速，若語音服務無回應時將自動改以穩定男聲備援朗讀"
                "（也可直接切換為「雲健」或「雲希」）。"
            )
        return ""

    @staticmethod
    def _match_voice_query(query: str, f5_voices: list[dict]) -> Optional[str]:
        edge_match = tts_engine.resolve_edge_voice_query(query)
        if edge_match:
            return edge_match

        q_low = query.lower()
        for fv in f5_voices:
            if q_low in (fv["id"].lower(), fv["raw_id"].lower(), fv["name"].lower()):
                return fv["id"]
        for fv in f5_voices:
            if q_low in f"{fv['raw_id']} {fv['name']} {fv['description']}".lower():
                return fv["id"]
        return None

    @staticmethod
    async def _send_voice_list_embed(
        ctx: commands.Context,
        *,
        current_voice_id: str,
        f5_voices: list[dict],
        title: str,
        f5_header: str,
        color: int,
        footer: str,
    ) -> None:
        lines = ["**⚡ 內建標準聲線**"]
        for vid, meta in tts_engine.EDGE_VOICES.items():
            mark = " ✅" if vid == current_voice_id else ""
            lines.append(f"• `{meta['short_name']}` — {meta['label']}{mark}")
        if f5_voices:
            lines.append(f5_header)
            for fv in f5_voices:
                mark = " ✅" if fv["id"] == current_voice_id else ""
                lines.append(f"• `{fv['raw_id']}` — {fv['name']}{mark}")
        embed = discord.Embed(title=title, description="\n".join(lines)[:3900], color=color)
        embed.set_footer(text=footer)
        await ctx.send(embed=embed)

    async def _handle_prosody_subcommand(
        self,
        ctx: commands.Context,
        spec: _VoiceDispatchSpec,
        *,
        is_pitch: bool,
        arg: Optional[str],
    ) -> None:
        if is_pitch:
            cur_fmt = tts_engine.format_edge_pitch(spec.current_pitch_hz)
            if arg is None:
                await ctx.send(f"{spec.pitch_info_prefix} `{cur_fmt}`（範圍 `-30` 到 `+30` Hz）。")
                return
            try:
                val = int(arg.strip().lower().replace("hz", ""))
            except ValueError:
                await ctx.send(spec.pitch_invalid_msg)
                return
            updated = await spec.save_fn(pitch=val)
            new_val = updated[spec.pitch_key]
            hint = self._prosody_limited_hint(updated[spec.voice_key], new_val)
            await ctx.send(f"{spec.pitch_updated_prefix} `{tts_engine.format_edge_pitch(new_val)}`。{hint}")
            return

        cur_fmt = tts_engine.format_edge_rate(spec.current_rate_pct)
        if arg is None:
            await ctx.send(f"{spec.rate_info_prefix} `{cur_fmt}`（範圍 `-50` 到 `+50` %）。")
            return
        try:
            val = int(arg.strip().replace("%", ""))
        except ValueError:
            await ctx.send(spec.rate_invalid_msg)
            return
        updated = await spec.save_fn(rate=val)
        new_val = updated[spec.rate_key]
        hint = self._prosody_limited_hint(updated[spec.voice_key], new_val)
        await ctx.send(f"{spec.rate_updated_prefix} `{tts_engine.format_edge_rate(new_val)}`。{hint}")

    async def _dispatch_voice_subcommand(
        self,
        ctx: commands.Context,
        spec: _VoiceDispatchSpec,
        f5_voices: list[dict],
        *,
        action: str,
        arg: Optional[str],
        full_query: str,
    ) -> None:
        if action in ("list", "ls", "清單"):
            await self._send_voice_list_embed(
                ctx,
                current_voice_id=spec.current_voice_id,
                f5_voices=f5_voices,
                title=spec.list_title,
                f5_header=spec.list_f5_header,
                color=spec.list_color,
                footer=spec.list_footer,
            )
            return

        if action in ("reset", "default", "預設", "重置"):
            reset_msg = await spec.reset_fn()
            await ctx.send(reset_msg)
            return

        if action in ("pitch", "音高", "音調"):
            await self._handle_prosody_subcommand(ctx, spec, is_pitch=True, arg=arg)
            return

        if action in ("rate", "speed", "語速"):
            await self._handle_prosody_subcommand(ctx, spec, is_pitch=False, arg=arg)
            return

        if action in ("clone", "upload", "add", "register", "建立", "上傳", "克隆"):
            await ctx.send(spec.clone_reject_msg)
            return

        matched_vid = self._match_voice_query(full_query, f5_voices)
        if matched_vid:
            updated = await spec.save_fn(voice_id=matched_vid)
            chosen_vid = updated[spec.voice_key]
            label = tts_engine.get_voice_display_label(chosen_vid, f5_voices)
            await ctx.send(f"{spec.switched_prefix}**{label}**")
            return

        await ctx.send(self._clip_reply(f"❌ 找不到符合 `{full_query[:80]}` 的{spec.not_found_hint}"))

    async def _handle_voice_command(
        self,
        ctx: commands.Context,
        sub: Optional[str] = None,
        arg: Optional[str] = None,
    ) -> None:
        pref = await asyncio.to_thread(
            tts_engine.get_user_voice_pref,
            ctx.author.id,
            self.settings.tts_default_voice,
        )
        f5_voices = await self.synthesizer.fetch_f5_voices(user_id=ctx.author.id)

        if not sub:
            view = VoiceSelectView(self, ctx.author.id, pref, f5_voices)
            view.message = await ctx.send(embed=view.build_embed(), view=view)
            return

        action = sub.strip().lower()
        if action in ("bot", "ai", "機器人"):
            await self._handle_bot_voice(ctx, arg)
            return

        mute_aliases = ("off", "mute", "quiet", "silent", "stop", "關閉", "靜音", "不讀", "不念", "停用")
        unmute_aliases = ("on", "unmute", "speak", "read", "開啟", "恢復", "取消靜音", "朗讀")
        toggle_aliases = ("toggle", "切換")
        if action in mute_aliases or action in unmute_aliases or action in toggle_aliases:
            if action in mute_aliases:
                target_muted = True
            elif action in unmute_aliases:
                target_muted = False
            else:
                target_muted = not bool(pref.get("proxy_muted"))
            await asyncio.to_thread(
                tts_engine.set_user_voice_pref,
                ctx.author.id,
                proxy_muted=target_muted,
                default_voice=self.settings.tts_default_voice,
            )
            if target_muted:
                await ctx.send(
                    "🔇 已設定**不要自動朗讀你打的文字**（改回開啟請輸入 `!voice on` 或從 `!voice` 面板點按鈕；明確使用 `!say` 仍可發聲）。"
                )
            else:
                await ctx.send("🔊 已恢復**自動朗讀你在文字區打的文字**。")
            return

        if self.settings.enable_user_voice_clone:
            clone_msg = (
                "🔒 語音克隆需經安全轉檔與機器人擁有者審核，無法由 `!voice` 直接註冊。\n"
                "👉 請改用 `!voiceclone [槽位] [名稱 |] <逐字稿>` 並**夾帶 3～15 秒音檔**送出申請（輸入 `!voiceclone` 可看說明）。"
            )
        else:
            clone_msg = (
                "🔒 私人語音克隆僅限機器人擁有者於本機伺服器設定，不開放線上註冊或上傳。"
                "如需申請專屬聲線請直接聯絡機器人擁有者（輸入 `!voiceclone` 查看申請說明），"
                "或使用 `!voice` 選擇內建／已授權聲線。"
            )

        async def _reset_personal() -> str:
            await asyncio.to_thread(tts_engine.reset_user_voice_pref, ctx.author.id)
            default_label = tts_engine.get_voice_display_label(self.settings.tts_default_voice)
            return f"🔄 已將你的聲線恢復為預設：**{default_label}**（語速 `+0%` / 音高 `+0Hz` / 自動代說：開啟）。"

        async def _save_personal(
            *,
            voice_id: Optional[str] = None,
            rate: Optional[int] = None,
            pitch: Optional[int] = None,
        ) -> dict:
            return await asyncio.to_thread(
                tts_engine.set_user_voice_pref,
                ctx.author.id,
                voice_id=voice_id,
                rate_pct=rate,
                pitch_hz=pitch,
                default_voice=self.settings.tts_default_voice,
            )

        spec = _VoiceDispatchSpec(
            current_voice_id=pref["voice_id"],
            current_rate_pct=pref["rate_pct"],
            current_pitch_hz=pref["pitch_hz"],
            voice_key="voice_id",
            rate_key="rate_pct",
            pitch_key="pitch_hz",
            list_title="🎙️ 可用聲線清單",
            list_f5_header="\n**🎙️ 特色／專屬聲線**",
            list_color=0x38BDF8,
            list_footer="輸入 !voice 開啟選單、!voice off/on 開關不讀字，或 !voiceclone 了解專屬語音克隆",
            pitch_invalid_msg="❌ 音高請輸入整數，例如 `!voice pitch +10` 或 `!voice pitch -8`（範圍 `-30..+30`）。",
            rate_invalid_msg="❌ 語速請輸入整數百分比，例如 `!voice rate +15` 或 `!voice rate -10`（範圍 `-50..+50`）。",
            pitch_info_prefix="ℹ️ 你目前的音高調整為",
            rate_info_prefix="ℹ️ 你目前的語速調整為",
            pitch_updated_prefix="✅ 已設定你的音高為",
            rate_updated_prefix="✅ 已設定你的語速為",
            switched_prefix="✅ 已切換你的聲線為：",
            not_found_hint="可用聲線，請輸入 `!voice` 開啟選單或 `!voice list` 查看清單。",
            clone_reject_msg=clone_msg,
            reset_fn=_reset_personal,
            save_fn=_save_personal,
        )
        full_query = f"{sub} {arg}".strip() if arg else sub.strip()
        await self._dispatch_voice_subcommand(
            ctx, spec, f5_voices, action=action, arg=arg, full_query=full_query
        )

    async def _handle_bot_voice(self, ctx: commands.Context, raw_arg: Optional[str] = None) -> None:
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return

        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        public_f5_voices = [
            fv for fv in await self.synthesizer.fetch_f5_voices(user_id=None) if fv.get("public")
        ]

        stripped = (raw_arg or "").strip()
        tokens = stripped.split(maxsplit=1)
        mode = "general"
        rest = stripped
        if tokens and tokens[0].lower() in ("jp", "日語", "日文"):
            mode = "jp"
            rest = tokens[1].strip() if len(tokens) > 1 else ""

        if not rest:
            view = BotVoiceSelectView(self, ctx.author.id, ctx.guild.id, g_cfg, public_f5_voices, mode=mode)
            view.message = await ctx.send(embed=view.build_embed(), view=view)
            return

        is_jp = mode == "jp"
        mode_label = "日語共學" if is_jp else "一般對話"
        voice_key = "ai_jp_voice_id" if is_jp else "ai_voice_id"
        rate_key = "ai_jp_rate_pct" if is_jp else "ai_rate_pct"
        pitch_key = "ai_jp_pitch_hz" if is_jp else "ai_pitch_hz"

        sub_tokens = rest.split(maxsplit=1)
        action = sub_tokens[0].lower()
        arg = sub_tokens[1].strip() if len(sub_tokens) > 1 else None

        async def _reset_bot() -> str:
            updated = await asyncio.to_thread(
                tts_engine.reset_guild_ai_voice,
                ctx.guild.id,
                target=mode,
                default_ai_jp=self.settings.tts_ai_auto_read_jp,
                default_ai_general=self.settings.tts_ai_auto_read_general,
                default_ai_voice=self.settings.tts_default_voice,
                default_ai_jp_voice=self.settings.tts_jp_voice,
            )
            default_vid = updated[voice_key]
            return (
                f"🔄 已將機器人**{mode_label}**聲線恢復為預設："
                f"**{tts_engine.get_voice_display_label(default_vid)}**（語速 `+0%` / 音高 `+0Hz`）。"
            )

        async def _save_bot(
            *,
            voice_id: Optional[str] = None,
            rate: Optional[int] = None,
            pitch: Optional[int] = None,
        ) -> dict:
            fields: dict = {}
            if voice_id is not None:
                fields[voice_key] = voice_id
            if rate is not None:
                fields[rate_key] = rate
            if pitch is not None:
                fields[pitch_key] = pitch
            return await self._update_guild_settings(ctx.guild.id, **fields)

        spec = _VoiceDispatchSpec(
            current_voice_id=g_cfg[voice_key],
            current_rate_pct=g_cfg[rate_key],
            current_pitch_hz=g_cfg[pitch_key],
            voice_key=voice_key,
            rate_key=rate_key,
            pitch_key=pitch_key,
            list_title=f"🤖 機器人可用聲線清單（{mode_label}）",
            list_f5_header="\n**🎙️ 公開特色聲線**",
            list_color=0xA855F7,
            list_footer="輸入 !voice bot 開啟下拉選單，或 !voice bot [jp] <關鍵字> 直接切換",
            pitch_invalid_msg="❌ 音高請輸入整數，例如 `!voice bot pitch +8` 或 `!voice bot jp pitch -5`。",
            rate_invalid_msg="❌ 語速請輸入整數百分比，例如 `!voice bot rate +15` 或 `!voice bot jp rate -15`。",
            pitch_info_prefix=f"ℹ️ 機器人**{mode_label}**目前的音高為",
            rate_info_prefix=f"ℹ️ 機器人**{mode_label}**目前的語速為",
            pitch_updated_prefix=f"✅ 已設定機器人**{mode_label}**音高為",
            rate_updated_prefix=f"✅ 已設定機器人**{mode_label}**語速為",
            switched_prefix=f"✅ 已設定機器人**{mode_label}**聲線為：",
            not_found_hint="公開聲線，請輸入 `!voice bot` 開啟選單或 `!voice bot list` 查看清單。",
            clone_reject_msg=(
                "🔒 機器人聲線僅可使用內建標準聲線或已公開的特色聲線。"
            ),
            reset_fn=_reset_bot,
            save_fn=_save_bot,
        )
        await self._dispatch_voice_subcommand(
            ctx, spec, public_f5_voices, action=action, arg=arg, full_query=rest
        )
